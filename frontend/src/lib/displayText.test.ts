import { describe, expect, it } from 'vitest'
import { restoreUtf8CjkLabel } from './displayText'

describe('restoreUtf8CjkLabel', () => {
  it('recovers a Chinese label from persisted Latin-1 UTF-8 bytes', () => {
    const corrupted = '\u00e8\u00be\u0093\u00e9\u0080\u0081\u00e7\u00ba\u00bf\u00e5\u0087\u00ba\u00e5\u0085\u00a5\u00e5\u008f\u00a3 4'
    expect(restoreUtf8CjkLabel(corrupted)).toBe('输送线出入口 4')
  })

  it('does not rewrite correct Unicode or unrelated Latin text', () => {
    expect(restoreUtf8CjkLabel('输送线出入口 4')).toBe('输送线出入口 4')
    expect(restoreUtf8CjkLabel('Café Port 4')).toBe('Café Port 4')
  })
})
