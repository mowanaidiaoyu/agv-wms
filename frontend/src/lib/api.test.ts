import { describe, expect, it } from 'vitest'
import { formatApiErrorPayload } from './api'

describe('formatApiErrorPayload', () => {
  it('formats FastAPI validation details without object string coercion', () => {
    expect(formatApiErrorPayload({
      detail: [{
        type: 'greater_than',
        loc: ['body', 'transfers', 0, 'quantity'],
        msg: 'Input should be greater than 0',
      }],
    }, '422 Unprocessable Entity')).toBe(
      'transfers.0.quantity: Input should be greater than 0',
    )
  })

  it('supports string detail, object detail, and message responses', () => {
    expect(formatApiErrorPayload({ detail: 'Topology missing' }, 'fallback')).toBe('Topology missing')
    expect(formatApiErrorPayload({ detail: { message: 'Invalid transfer' } }, 'fallback')).toBe('Invalid transfer')
    expect(formatApiErrorPayload({ message: 'Request failed' }, 'fallback')).toBe('Request failed')
  })
})
