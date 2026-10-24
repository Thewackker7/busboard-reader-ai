import test from 'node:test'
import assert from 'node:assert/strict'
import { runWithBusy, submitManualVerification, submitVerification } from '../src/api.js'

test('loading state resets after a failed verification', async () => {
  const states = []
  await assert.rejects(runWithBusy((value) => states.push(value), async () => { throw new Error('Timed out') }), /Timed out/)
  assert.deepEqual(states, [true, false])
})

test('manual review sends confirmed text without an image request', async () => {
  let body
  await submitManualVerification({
    apiBase: 'http://localhost:8000', origin: 'Ottapalam', destination: 'Thrissur',
    confirmedDestination: 'Thrissur', originalDetectedText: 'THRISSURR',
    fetchImpl: async (_url, init) => { body = init.body; return { ok: true, json: async () => ({ status: 'match' }) } },
  })
  assert.equal(body.get('confirmed_destination'), 'Thrissur')
  assert.equal(body.get('original_detected_text'), 'THRISSURR')
  assert.equal(body.get('photo'), null)
})

test('successful verification sends multipart field names and returns JSON', async () => {
  let request
  const result = await submitVerification({
    apiBase: 'http://localhost:8000', origin: 'Ottapalam', destination: 'Thrissur',
    photo: new Blob(['image'], { type: 'image/png' }),
    fetchImpl: async (url, init) => {
      request = { url, init }
      return { ok: true, json: async () => ({ status: 'match' }) }
    },
  })
  assert.equal(request.url, 'http://localhost:8000/api/verify')
  assert.equal(request.init.method, 'POST')
  assert.equal(request.init.body.get('origin'), 'Ottapalam')
  assert.equal(request.init.body.get('destination'), 'Thrissur')
  assert.equal(request.init.body.get('photo').type, 'image/png')
  assert.deepEqual(result, { status: 'match' })
})
