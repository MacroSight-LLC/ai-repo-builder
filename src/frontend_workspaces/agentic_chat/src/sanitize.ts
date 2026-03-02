/**
 * Minimal HTML sanitizer for marked() output.
 * Strips <script> tags, event handlers (onerror, onload, etc.),
 * and javascript: URIs to prevent XSS from agent/server content.
 *
 * For production use with untrusted content, consider installing DOMPurify.
 */
export function sanitizeHtml(html: string): string {
  return html
    // Remove <script>...</script> blocks (including multiline)
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    // Remove inline event handlers (on*)
    .replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    // Remove javascript: URIs in href/src/action attributes
    .replace(/(href|src|action)\s*=\s*["']?\s*javascript:/gi, "$1=\"\"");
}
