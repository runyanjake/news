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
        DOCKER_BUILDKIT      = '1'

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
                    docker buildx version >/dev/null 2>&1 || fail "docker buildx plugin is not installed (needed for BuildKit)"
                    for f in docker-compose.yml Dockerfile Dockerfile.test config/sources.yaml nginx/nginx.conf; do
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
                '''
            }
        }

        stage('Build') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    docker compose build --pull || { echo "BUILD ERROR: image build failed" >&2; exit 1; }
                '''
            }
        }

        // Dockerfile.test layers lint and tests onto the images just built, so the checks run on exactly what deploys.
        // Sources reach the daemon as the build context (bind mounts break when Jenkins runs in a container), and
        // layer caching skips dependency installs, and even passing tests, whose inputs haven't changed.
        stage('Lint & Test') {
            parallel {
                stage('Backend') {
                    steps {
                        sh '''#!/bin/bash
                            set -euo pipefail
                            docker build --progress=plain -f Dockerfile.test --target backend . \
                                || { echo "LINT ERROR: backend lint, tests or config validation failed" >&2; exit 1; }
                        '''
                    }
                }
                stage('Frontend') {
                    steps {
                        sh '''#!/bin/bash
                            set -euo pipefail
                            docker build --progress=plain -f Dockerfile.test --target renderer . \
                                || { echo "LINT ERROR: frontend syntax check or tests failed" >&2; exit 1; }
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

        stage('Deploy') {
            steps {
                sh '''#!/bin/bash
                    set -euo pipefail
                    docker compose up -d --no-build || { echo "DEPLOY ERROR: containers failed to start" >&2; exit 1; }
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
