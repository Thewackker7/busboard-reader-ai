export const VOICE_COPY = {
  en: {
    match: 'This bus appears to match your selected journey.',
    mismatch: 'This bus may not match your selected journey. Please check the destination board.',
    unable_to_verify: 'We could not verify this bus. Please check the destination board manually.',
  },
  ml: {
    match: 'ഈ ബസ് നിങ്ങളുടെ തിരഞ്ഞെടുത്ത യാത്രയുമായി പൊരുത്തപ്പെടുന്നതായി തോന്നുന്നു.',
    mismatch: 'ഈ ബസ് നിങ്ങളുടെ തിരഞ്ഞെടുത്ത യാത്രയുമായി പൊരുത്തപ്പെടാതിരിക്കാം. ദയവായി ബോർഡ് പരിശോധിക്കുക.',
    unable_to_verify: 'ഈ ബസ് സ്ഥിരീകരിക്കാനായില്ല. ദയവായി ബോർഡ് നേരിട്ട് പരിശോധിക്കുക.',
  },
}

export function resultSpeech(result, language) {
  if (!result) return ''
  const copy = language === 'ml-IN' ? VOICE_COPY.ml : VOICE_COPY.en
  const base = copy[result.status] || copy.unable_to_verify
  if (language === 'en-IN' && result.detected_destination) return `${base} The board reads ${result.detected_destination}.`
  return base
}

export function createAudioPlayback(blob, { AudioCtor = Audio, createObjectURL = URL.createObjectURL } = {}) {
  const url = createObjectURL(blob)
  const audio = new AudioCtor(url)
  return { audio, url }
}

export function stopAudioPlayback(audio, url, { revokeObjectURL = URL.revokeObjectURL } = {}) {
  if (audio) { audio.pause(); audio.currentTime = 0 }
  if (url) revokeObjectURL(url)
}
