const CJK_TEXT = /[\u3400-\u4dbf\u4e00-\u9fff]/u

/**
 * Restore Chinese text whose UTF-8 bytes were persisted as Latin-1 code points.
 *
 * The conversion is deliberately conservative: every non-ASCII code point in
 * the source must be a byte, the byte sequence must be valid UTF-8, and the
 * decoded result must contain CJK text. Correct Unicode and unrelated labels
 * are returned byte-for-byte unchanged.
 */
export function restoreUtf8CjkLabel(value: string): string {
  const codePoints = Array.from(value, (character) => character.codePointAt(0) ?? 0)
  if (!codePoints.some((codePoint) => codePoint >= 0x80) || codePoints.some((codePoint) => codePoint > 0xff)) {
    return value
  }

  try {
    const encodedBytes = codePoints
      .map((codePoint) => `%${codePoint.toString(16).padStart(2, '0')}`)
      .join('')
    const decoded = decodeURIComponent(encodedBytes)
    return decoded !== value && CJK_TEXT.test(decoded) ? decoded : value
  } catch {
    return value
  }
}
