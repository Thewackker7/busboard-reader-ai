import test from 'node:test'
import assert from 'node:assert/strict'
import { requestSpeech } from '../src/api.js'
import { createAudioPlayback, resultSpeech, stopAudioPlayback } from '../src/voice.js'

test('result speech uses the selected language and actual board text', () => {
  assert.match(resultSpeech({ status: 'match', detected_destination: 'Thrissur' }, 'en-IN'), /Thrissur/)
  assert.match(resultSpeech({ status: 'mismatch' }, 'ml-IN'), /പൊരുത്തപ്പെടാതിരിക്കാം/)
})

test('speech request sends JSON to the local API', async () => {
  let request
  const blob = new Blob(['RIFF'], { type: 'audio/wav' })
  const result = await requestSpeech({
    apiBase: 'http://localhost:8000', text: 'ഈ ബസ്', language: 'ml-IN',
    fetchImpl: async (url, init) => { request = { url, init }; return { ok: true, blob: async () => blob } },
  })
  assert.equal(result, blob)
  assert.equal(request.url, 'http://localhost:8000/api/speech')
  assert.deepEqual(JSON.parse(request.init.body), { text: 'ഈ ബസ്', language: 'ml-IN' })
})

test('speech request exposes local API network and HTTP failures', async () => {
  await assert.rejects(requestSpeech({ apiBase: 'x', text: 'hi', language: 'en-IN', fetchImpl: async () => { throw new Error('Network unavailable') } }), /Network unavailable/)
  await assert.rejects(requestSpeech({ apiBase: 'x', text: 'hi', language: 'en-IN', fetchImpl: async () => ({ ok: false, json: async () => ({ detail: 'Sarvam unavailable' }) }) }), /Sarvam unavailable/)
})

test('audio playback starts and stop releases resources', () => {
  const events = []
  class FakeAudio { constructor(url) { this.url = url; this.currentTime = 4 } pause() { events.push('pause') } }
  const { audio, url } = createAudioPlayback(new Blob(['RIFF']), { AudioCtor: FakeAudio, createObjectURL: () => 'blob:test' })
  assert.equal(audio.url, 'blob:test')
  stopAudioPlayback(audio, url, { revokeObjectURL: (value) => events.push(value) })
  assert.equal(audio.currentTime, 0)
  assert.deepEqual(events, ['pause', 'blob:test'])
})
