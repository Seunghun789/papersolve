// Next's internal URL may use localhost even when the browser used 127.0.0.1.
// Compare against the actual Host header; never accept an absent/null Origin.
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin');
  const host = request.headers.get('host');
  if (!origin || !host) return false;
  try {
    const source = new URL(origin);
    const protocol = request.headers.get('x-forwarded-proto') || new URL(request.url).protocol.slice(0, -1);
    return ['http', 'https'].includes(protocol) && source.origin === `${protocol}://${host}`;
  } catch { return false; }
}
