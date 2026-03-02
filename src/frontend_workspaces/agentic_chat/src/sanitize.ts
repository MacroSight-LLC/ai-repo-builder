/**
 * HTML sanitizer for marked() output.
 * Strips <script> tags, event handlers (onerror, onload, etc.),
 * javascript:/data: URIs, and handles HTML-entity encoded bypasses.
 *
 * For production use with untrusted content, consider installing DOMPurify.
 */

/** Decode HTML entities so encoded payloads get caught by subsequent regexes. */
function decodeEntities(html: string): string {
  return html
    .replace(/&#x([0-9a-f]+);?/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
    .replace(/&#(\d+);?/g, (_, dec) => String.fromCharCode(parseInt(dec, 10)));
}

export function sanitizeHtml(html: string): string {
  // First pass: decode HTML entities to catch encoded attacks
  let decoded = decodeEntities(html);

  return (
    decoded
      // Remove <script>...</script> blocks (including multiline)
      .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
      // Remove <iframe>, <object>, <embed>, <form> tags entirely
      .replace(/<(iframe|object|embed|form)\b[^>]*>[\s\S]*?<\/\1>/gi, "")
      .replace(/<(iframe|object|embed|form)\b[^>]*\/?>/gi, "")
      // Remove inline event handlers (on*) — also catches forward-slash variants like <svg/onload=...>
      .replace(/[\s/]+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      // Remove javascript: URIs in href/src/action attributes
      .replace(/(href|src|action)\s*=\s*["']?\s*javascript\s*:/gi, '$1=""')
      // Remove data: URIs (can embed executable HTML/JS)
      .replace(/(href|src)\s*=\s*["']?\s*data\s*:/gi, '$1=""')
  );
}
