export async function submitVerification({ apiBase, origin, destination, photo, signal, fetchImpl = fetch }) {
  const form = new FormData()
  form.append('origin', origin)
  form.append('destination', destination)
  form.append('photo', photo)
  const response = await fetchImpl(`${apiBase}/api/verify`, { method: 'POST', body: form, signal })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Could not verify this bus. Please try again.')
  return data
}

export async function submitManualVerification({ apiBase, origin, destination, confirmedDestination, originalDetectedText, fetchImpl = fetch }) {
  const form = new FormData()
  form.append('origin', origin)
  form.append('destination', destination)
  form.append('confirmed_destination', confirmedDestination)
  form.append('original_detected_text', originalDetectedText || '')
  const response = await fetchImpl(`${apiBase}/api/verify-manual`, { method: 'POST', body: form })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Could not recheck the destination.')
  return data
}

export async function requestSpeech({ apiBase, text, language, signal, fetchImpl = fetch }) {
  const response = await fetchImpl(`${apiBase}/api/speech`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, language }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not generate local speech audio.')
  }
  return response.blob()
}

export async function requestRouteAssist({ apiBase, origin, destination, signal, fetchImpl = fetch }) {
  const response = await fetchImpl(`${apiBase}/api/route-assist`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin, destination }),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Gemini could not check this route.')
  return data
}

export async function runWithBusy(setBusy, work) {
  setBusy(true)
  try {
    return await work()
  } finally {
    setBusy(false)
  }
}
