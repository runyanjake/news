pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
    }

    environment {
        COMPOSE_PROJECT_NAME = 'pws-news'
        TZ                   = 'America/Los_Angeles'
        RENDER_TIME          = '08:00'
        HTTP_PORT            = '8080'
        LLM_BASE_URL         = 'http://llm-server:8080/v1'
        LLM_MODEL            = 'llama3.1'
        LLM_TIMEOUT_SECONDS  = '120'
        PYTHON_IMAGE         = 'python:3.12-slim'
        NODE_IMAGE           = 'node:20-slim'
        RUFF_VERSION         = '0.17.2'

        ADMIN_TOKEN          = credentials('news-admin-token')
        LLM_API_KEY          = credentials('llama_cpp-llm-api-key')
        DISCORD_WEBHOOK      = credentials('discord-pws-builds-channel-webhook')
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Preflight') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    fail() { echo "PREFLIGHT ERROR: $*" >&2; exit 1; }

                    for var in ADMIN_TOKEN DISCORD_WEBHOOK; do
                        [ -n "${!var:-}" ] || fail "Jenkins credential for $var is empty"
                    done
                    command -v docker >/dev/null || fail "docker is not installed on this agent"
                    docker info >/dev/null 2>&1 || fail "docker daemon is not reachable"
                    docker compose version >/dev/null 2>&1 || fail "docker compose plugin is not installed"
                    for f in docker-compose.yml config/sources.yaml nginx/nginx.conf; do
                        [ -f "$f" ] || fail "missing required file: $f"
                    done

                    # docker-compose.yml loads runtime config from .env; regenerate it from Jenkins on every build.
                    umask 077
                    {
                        for var in TZ RENDER_TIME ADMIN_TOKEN LLM_BASE_URL LLM_MODEL LLM_API_KEY LLM_TIMEOUT_SECONDS HTTP_PORT; do
                            printf '%s=%s\\n' "$var" "${!var}"
                        done
                    } > .env

                    docker compose config --quiet || fail "docker-compose.yml is invalid"
                    docker run --rm -v "$WORKSPACE:/src:ro" -w /src/backend -e PYTHONDONTWRITEBYTECODE=1 "$PYTHON_IMAGE" sh -c '
                        pip install --quiet --disable-pip-version-check --root-user-action=ignore -r requirements.txt &&
                        python -c "from app.config import load_config; c = load_config(\\"/src/config/sources.yaml\\"); assert c.sources, \\"no sources configured\\""
                    ' || fail "config/sources.yaml is malformed"
                '''
            }
        }

        stage('Lint & Type-check') {
            parallel {
                stage('Backend') {
                    steps {
                        sh '''#!/bin/bash
                            set -euo pipefail
                            docker run --rm -v "$WORKSPACE/backend:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 "$PYTHON_IMAGE" sh -c "
                                pip install --quiet --disable-pip-version-check --root-user-action=ignore -r requirements-dev.txt ruff==$RUFF_VERSION &&
                                ruff check --no-cache --select E9,F . &&
                                python -m pytest -q -p no:cacheprovider
                            " || { echo "LINT ERROR: backend lint or tests failed" >&2; exit 1; }
                        '''
                    }
                }
                stage('Frontend') {
                    steps {
                        sh '''#!/bin/bash
                            set -euo pipefail
                            docker run --rm -v "$WORKSPACE/frontend:/src:ro" -w /src "$NODE_IMAGE" sh -c '
                                find src test -name "*.js" -print0 | xargs -0 -n1 node --check &&
                                npm test --silent
                            ' || { echo "LINT ERROR: frontend syntax check or tests failed" >&2; exit 1; }
                        '''
                    }
                }
            }
        }

        stage('Teardown') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    docker compose down --remove-orphans --timeout 30 \
                        || { echo "TEARDOWN ERROR: could not stop the previous deployment" >&2; exit 1; }
                '''
            }
        }

        stage('Build & Deploy') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    mkdir -p data/reports data/site
                    docker compose build --pull || { echo "BUILD ERROR: image build failed" >&2; exit 1; }
                    docker compose up -d || { echo "DEPLOY ERROR: containers failed to start" >&2; exit 1; }
                '''
            }
        }

        stage('Health Check') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    for attempt in $(seq 1 30); do
                        if [ -n "$(docker compose ps -q --status exited --status dead --status restarting)" ]; then
                            echo "HEALTH ERROR: a container crashed during startup" >&2
                            docker compose ps -a
                            exit 1
                        fi
                        if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" >/dev/null 2>&1; then
                            echo "Backend is live after $attempt attempt(s)"
                            exit 0
                        fi
                        sleep 2
                    done
                    echo "HEALTH ERROR: backend did not report healthy within 60s" >&2
                    exit 1
                '''
            }
        }

        stage('Smoke Test') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    fail() { echo "SMOKE TEST ERROR: $*" >&2; exit 1; }
                    # Requests go through nginx so the proxy and static config are exercised too.
                    fetch() { docker compose exec -T nginx wget -qO- "http://localhost$1"; }

                    health=$(fetch /api/health) || fail "GET /api/health via nginx failed"
                    echo "$health" | grep -q '"status":"ok"' || fail "unexpected /api/health response: $health"

                    reports=$(fetch /api/reports) || fail "GET /api/reports via nginx failed"
                    echo "$reports" | grep -Eq '^\\[.*\\]$' || fail "/api/reports did not return a JSON list: $reports"

                    echo "Smoke test passed"
                '''
            }
        }
    }

    post {
        always {
            script {
                def result = currentBuild.currentResult
                def emoji = result == 'SUCCESS' ? ':green_circle:' : (result == 'FAILURE' ? ':red_circle:' : ':yellow_circle:')
                def branch = env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'Main/Manual'
                def duration = currentBuild.durationString.replace(' and no weeks', '').replace(' and counting', '')

                def commits = []
                for (changeSet in currentBuild.changeSets) {
                    for (entry in changeSet.items) {
                        commits << "> ${entry.msg} (by *${entry.author.fullName}*)"
                    }
                }
                def commitText = commits ? commits.join('\n') : 'No recent changes detected.'

                def discordDescription = """**Status:** ${emoji} ${result}
**Branch:** `${branch}`
**Duration:** :stopwatch: ${duration}

**Commits:**
${commitText}"""

                discordSend(
                    webhookURL: env.DISCORD_WEBHOOK,
                    title: "📦 Build Alert: ${env.JOB_NAME} [Build #${env.BUILD_NUMBER}]",
                    link: "${env.BUILD_URL}",
                    result: "${currentBuild.currentResult}",
                    description: discordDescription
                )
            }
        }
        failure {
            sh '''#!/bin/bash
                echo "=== Container state ==="
                docker compose ps -a || true
                echo "=== Recent logs ==="
                docker compose logs --no-color --timestamps --tail 200 || true
            '''
        }
    }
}
