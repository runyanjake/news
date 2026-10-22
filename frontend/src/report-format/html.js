const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escape(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Only allow http(s) links from feeds into the page. */
export function safeUrl(url) {
  return /^https?:\/\//i.test(String(url ?? '')) ? escape(url) : '#';
}
