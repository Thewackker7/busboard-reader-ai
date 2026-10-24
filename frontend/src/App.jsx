import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeftRight, ArrowRight, BusFront, Camera, CheckCircle2, CircleAlert, Clock3,
  Globe2, ImagePlus, Languages, MapPin, RotateCcw, ShieldCheck, Sparkles, Square,
  Upload, Volume2, X, XCircle,
} from 'lucide-react'
import { requestRouteAssist, requestSpeech, runWithBusy, submitManualVerification, submitVerification } from './api'
import { createAudioPlayback, resultSpeech, stopAudioPlayback } from './voice'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const allowedTypes = ['image/jpeg', 'image/png', 'image/webp']

function GeminiRouteSuggestion({ routeAssist }) {
  if (!routeAssist) return null
  return <div className="gemini-assist" aria-live="polite">
    <div className="gemini-assist-title"><Sparkles size={17}/><div><strong>Gemini route suggestion</strong><span>Model confidence (not verified): {routeAssist.confidence}</span></div></div>
    <p>{routeAssist.summary || 'Gemini returned a possible major-stop corridor.'}</p>
    {routeAssist.major_stops?.length > 0 ? <ol>{routeAssist.major_stops.map((stop, i) => <li key={`${stop.name}-${i}`}><span>{stop.name}</span><small>{stop.evidence === 'listed_on_matching_timetable_route' ? 'Also listed on a matching timetable route' : stop.evidence === 'listed_elsewhere_in_timetable_data' ? 'Name appears elsewhere in the timetable data; this route is not confirmed' : 'Gemini suggestion only; not listed in the timetable dataset'}</small></li>)}</ol> : <p>Gemini could not suggest reliable intermediate stops for this route.</p>}
    {routeAssist.timetable_routes?.length > 0 && <div className="assist-dataset"><strong>Matching imported timetable paths</strong>{routeAssist.timetable_routes.map((stops, i) => <span key={`${stops.join('-')}-${i}`}>{stops.join(' → ')}</span>)}</div>}
    <p className="assist-notice">{routeAssist.notice}</p>
  </div>
}

function normalizePlaceName(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').trim().replace(/\s+/g, ' ')
}

function resultMeta(status) {
  if (status === 'match') return { Icon: CheckCircle2, tone: 'success', label: 'Route data match' }
  if (status === 'mismatch') return { Icon: XCircle, tone: 'warning', label: 'Identified route does not match' }
  return { Icon: CircleAlert, tone: 'neutral', label: 'Unable to verify' }
}

export default function App() {
  const [origin, setOrigin] = useState('Ottapalam')
  const [destination, setDestination] = useState('Thrissur')
  const [photo, setPhoto] = useState(null)
  const [preview, setPreview] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [apiHealth, setApiHealth] = useState('checking')
  const [routesNotice, setRoutesNotice] = useState('')
  const [keralaPlaces, setKeralaPlaces] = useState([])
  const [districtPlaces, setDistrictPlaces] = useState([])
  const [activePlaceInput, setActivePlaceInput] = useState(null)
  const [busOptions, setBusOptions] = useState(null)
  const [busLoading, setBusLoading] = useState(false)
  const [busSearchError, setBusSearchError] = useState('')
  const [busSearchNotice, setBusSearchNotice] = useState('')
  const [routeAssist, setRouteAssist] = useState(null)
  const [routeAssistLoading, setRouteAssistLoading] = useState(false)
  const [routeAssistError, setRouteAssistError] = useState('')
  const [language, setLanguage] = useState('en-IN')
  const [speaking, setSpeaking] = useState(false)
  const [speechLoading, setSpeechLoading] = useState(false)
  const [voiceNotice, setVoiceNotice] = useState('')
  const [reviewText, setReviewText] = useState('')
  const [reviewOpen, setReviewOpen] = useState(false)
  const inputRef = useRef(null)
  const abortRef = useRef(null)
  const audioRef = useRef(null)
  const audioUrlRef = useRef('')
  const speechAbortRef = useRef(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/health`).then((r) => { if (!r.ok) throw new Error(); return r.json() }).then(() => setApiHealth('online')).catch(() => setApiHealth('offline'))
    fetch(`${API_BASE}/api/routes`).then((r) => r.ok ? r.json() : null).then((data) => data && setRoutesNotice(data.notice || '')).catch(() => {})
    fetch(`${API_BASE}/api/places`).then((r) => r.ok ? r.json() : null).then((data) => data && setDistrictPlaces(data.places || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!activePlaceInput) return undefined
    const query = activePlaceInput === 'origin' ? origin : destination
    const controller = new AbortController()
    const timer = setTimeout(() => {
      fetch(`${API_BASE}/api/places?q=${encodeURIComponent(query)}`, { signal: controller.signal })
        .then((response) => response.ok ? response.json() : null)
        .then((data) => data && setKeralaPlaces(data.places || []))
        .catch(() => {})
    }, 120)
    return () => { clearTimeout(timer); controller.abort() }
  }, [activePlaceInput, origin, destination])

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); stopSpeaking() }, [preview])

  const canSubmit = Boolean(origin.trim() && destination.trim() && photo && !busy)
  const meta = result ? resultMeta(result.status) : null
  const originInfo = districtPlaces.find((place) => normalizePlaceName(place.name) === normalizePlaceName(origin))
  const destinationInfo = districtPlaces.find((place) => normalizePlaceName(place.name) === normalizePlaceName(destination))

  async function findPrivateBuses() {
    setBusLoading(true)
    setBusOptions(null)
    setBusSearchError('')
    setBusSearchNotice('')
    setRouteAssist(null)
    setRouteAssistError('')
    try {
      const params = new URLSearchParams({ origin, destination })
      const response = await fetch(`${API_BASE}/api/buses?${params}`)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not load bus schedules.')
      setBusOptions(data.options || [])
      setBusSearchNotice(data.notice || '')
    } catch (err) {
      setBusOptions([])
      setBusSearchError(err.message || 'Could not load bus schedules. Check that the backend is running.')
    } finally {
      setBusLoading(false)
    }
  }

  async function checkStopsWithGemini() {
    setRouteAssistLoading(true)
    setRouteAssist(null)
    setRouteAssistError('')
    try {
      const data = await requestRouteAssist({ apiBase: API_BASE, origin, destination })
      setRouteAssist(data)
    } catch (err) {
      setRouteAssistError(err.message || 'Gemini could not check this route.')
    } finally {
      setRouteAssistLoading(false)
    }
  }

  function onChooseFile(file) {
    setError('')
    setResult(null)
    setReviewOpen(false)
    if (!file) return
    if (!allowedTypes.includes(file.type)) return setError('Choose a JPG, PNG, or WebP image.')
    if (file.size === 0) return setError('That image file is empty. Choose another photo.')
    if (file.size > 10 * 1024 * 1024) return setError('Image must be smaller than 10 MB.')
    if (preview) URL.revokeObjectURL(preview)
    setPhoto(file)
    setPreview(URL.createObjectURL(file))
  }

  async function verifyBus(event) {
    event.preventDefault()
    setError('')
    setResult(null)
    if (!origin.trim() || !destination.trim()) return setError('Enter both a starting point and destination.')
    if (!photo) return setError('Upload a bus destination-board photo first.')
    abortRef.current = new AbortController()
    try {
      const data = await runWithBusy(setBusy, () => submitVerification({ apiBase: API_BASE, origin, destination, photo, signal: abortRef.current.signal }))
      setResult(data)
      setReviewText(data.detected_destination || '')
    } catch (err) {
      setError(err.name === 'AbortError' ? 'Scan cancelled. You can try again.' : (err.message || 'Something went wrong. Check that the backend is running.'))
    } finally {
      abortRef.current = null
    }
  }

  async function confirmReview() {
    setError('')
    if (!reviewText.trim()) return setError('Enter the destination shown on the board.')
    try {
      const data = await runWithBusy(setBusy, () => submitManualVerification({
        apiBase: API_BASE, origin, destination, confirmedDestination: reviewText,
        originalDetectedText: result?.scan?.original_transcription || result?.detected_destination || '',
      }))
      setResult(data)
      setReviewOpen(false)
    } catch (err) { setError(err.message || 'Could not recheck the destination.') }
  }

  function resetScan() {
    setResult(null); setError(''); setReviewOpen(false); setPhoto(null)
    if (preview) URL.revokeObjectURL(preview)
    setPreview('')
    if (inputRef.current) inputRef.current.value = ''
  }

  function stopSpeaking() {
    speechAbortRef.current?.abort()
    speechAbortRef.current = null
    stopAudioPlayback(audioRef.current, audioUrlRef.current)
    audioRef.current = null
    audioUrlRef.current = ''
    setSpeechLoading(false); setSpeaking(false); setVoiceNotice('Speech stopped.')
  }

  async function readResult() {
    const text = resultSpeech(result, language)
    if (!text) return setVoiceNotice('There is no result text to read yet.')
    stopAudioPlayback(audioRef.current, audioUrlRef.current)
    audioRef.current = null; audioUrlRef.current = ''
    speechAbortRef.current?.abort()
    const controller = new AbortController()
    speechAbortRef.current = controller
    setSpeechLoading(true); setSpeaking(false); setVoiceNotice('Generating local audio…')
    try {
      const blob = await requestSpeech({ apiBase: API_BASE, text, language, signal: controller.signal })
      if (controller.signal.aborted) return
      const playback = createAudioPlayback(blob)
      audioRef.current = playback.audio; audioUrlRef.current = playback.url
      playback.audio.onended = () => { stopAudioPlayback(null, audioUrlRef.current); audioRef.current = null; audioUrlRef.current = ''; setSpeaking(false); setVoiceNotice('Finished reading result.') }
      playback.audio.onerror = () => { stopAudioPlayback(null, audioUrlRef.current); audioRef.current = null; audioUrlRef.current = ''; setSpeaking(false); setVoiceNotice('Audio was generated, but this browser could not play the WAV file.') }
      await playback.audio.play()
      setSpeaking(true); setVoiceNotice('Reading result aloud.')
    } catch (err) {
      if (err.name !== 'AbortError') setVoiceNotice(err.message || 'Could not generate local speech audio.')
    } finally {
      if (!controller.signal.aborted) setSpeechLoading(false)
      if (speechAbortRef.current === controller) speechAbortRef.current = null
    }
  }

  return <main className="app-shell">
    <a className="skip-link" href="#planner">Skip to journey planner</a>
    <header className="topbar">
      <a className="brand" href="#top"><span className="brand-icon"><BusFront size={22} /></span><span><strong>BusBoard</strong><small>Reader AI</small></span></a>
      <div className="header-tools">
        <span className={`connection ${apiHealth}`}><i />{apiHealth === 'online' ? 'Service ready' : apiHealth === 'offline' ? 'Service unavailable' : 'Checking service'}</span>
        <label className="language-control"><Languages size={16} /><span className="sr-only">Result language</span><select value={language} onChange={(e) => { setLanguage(e.target.value); setVoiceNotice('') }}><option value="en-IN">English</option><option value="ml-IN">മലയാളം</option></select></label>
        <button className="header-voice" type="button" onClick={speaking || speechLoading ? stopSpeaking : readResult} disabled={!result} aria-label={speaking || speechLoading ? 'Stop reading result aloud' : 'Read current result aloud'}><Volume2 size={16}/><span>{speaking || speechLoading ? 'Stop' : 'Voice'}</span></button>
      </div>
    </header>

    <section className="intro" id="top"><div><p className="kicker">LOCAL JOURNEY CHECKER</p><h1>Check the board.<br /><em>Know your next move.</em></h1><p className="intro-copy">Read a bus destination board, then compare it against the route records available in this prototype.</p></div><div className="intro-note"><ShieldCheck size={20}/><div><strong>Limited route data</strong><span>Illustrative routes only. Always confirm with the driver or conductor.</span></div></div></section>

    <section className="planner" id="planner" aria-labelledby="planner-title">
      <div className="section-title"><span>01</span><div><p className="kicker">PLAN YOUR JOURNEY</p><h2 id="planner-title">Where are you going?</h2></div></div>
      <form onSubmit={verifyBus}>
        <div className="journey-card">
          <label className="field"><span>Starting point</span><div className="input-wrap"><MapPin size={17}/><input list="kerala-places" value={origin} onFocus={() => setActivePlaceInput('origin')} onChange={(e) => { setOrigin(e.target.value); setRouteAssist(null) }} placeholder="Type a place or bus stop" autoComplete="off" /></div>{originInfo?.district && <small className="district-hint">{originInfo.district} district{originInfo.state && originInfo.state !== 'Kerala' ? ` · ${originInfo.state}` : ''}</small>}</label>
          <button className="swap-button" type="button" onClick={() => { setOrigin(destination); setDestination(origin) }} aria-label="Swap starting point and destination"><ArrowLeftRight size={19}/></button>
          <label className="field"><span>Destination</span><div className="input-wrap"><MapPin size={17}/><input list="kerala-places" value={destination} onFocus={() => setActivePlaceInput('destination')} onChange={(e) => { setDestination(e.target.value); setRouteAssist(null) }} placeholder="Type a place or bus stop" autoComplete="off" /></div>{destinationInfo?.district && <small className="district-hint">{destinationInfo.district} district{destinationInfo.state && destinationInfo.state !== 'Kerala' ? ` · ${destinationInfo.state}` : ''}</small>}</label>
          <datalist id="kerala-places">{keralaPlaces.map((place) => <option key={`${place.name}-${place.district || ''}`} value={place.name} label={place.district ? `${place.district} district` : 'Kerala bus stop'}/>)}</datalist>
        </div>
        <p className="helper">Choose a place suggestion; its district appears when mapped. Search currently installed timetable data below; suggested stops are not service confirmations.</p>

        <div className="section-title scanner-title"><span>02</span><div><p className="kicker">SCAN A BUS BOARD</p><h2>Upload a clear photo</h2></div></div>
        <div className={`upload-card ${preview ? 'has-preview' : ''}`} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onChooseFile(e.dataTransfer.files?.[0]) }}>
          {!preview ? <button type="button" className="upload-zone" onClick={() => inputRef.current?.click()}><span className="upload-icon"><Camera size={24}/></span><strong>Drop a board photo here</strong><span>or choose an image from your device</span><b><ImagePlus size={15}/> Choose photo</b></button> : <div className="preview-layout"><img className="photo-preview" src={preview} alt="Selected bus destination board"/><div><p className="file-ready"><CheckCircle2 size={15}/> Photo ready</p><strong>{photo?.name}</strong><span>{(photo?.size / 1024 / 1024).toFixed(2)} MB · ready to scan</span><button type="button" className="link-button" onClick={() => inputRef.current?.click()}>Replace photo</button></div><button type="button" className="icon-button" onClick={resetScan} aria-label="Remove selected photo"><X size={19}/></button></div>}
          <input ref={inputRef} className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(e) => onChooseFile(e.target.files?.[0])}/>
        </div>
        <p className="helper">JPG, PNG or WebP · maximum 10 MB · camera supported on compatible phones.</p>
        {error && <div className="error-message" role="alert"><CircleAlert size={18}/><span>{error}</span></div>}
        <div className="action-row"><button className="primary-button" type="submit" disabled={!canSubmit}>{busy ? <><span className="spinner"/> Reading board and checking journey…</> : <>Check this bus <ArrowRight size={18}/></>}</button><button className="secondary-button" type="button" onClick={findPrivateBuses} disabled={!origin.trim() || !destination.trim() || busLoading || busy}>{busLoading ? <><span className="spinner"/> Searching schedules…</> : <><Clock3 size={16}/> Find listed bus routes</>}</button>{busy && <button className="secondary-button" type="button" onClick={() => abortRef.current?.abort()}>Cancel scan</button>}</div>
        <p className="privacy-note"><ShieldCheck size={14}/> Your image is sent to Gemini for recognition and is not stored by this prototype.</p>
      </form>
    </section>

    {busOptions !== null && <section className="schedule-card" aria-labelledby="schedule-title" aria-live="polite">
      <div className="schedule-heading"><div><p className="kicker">PUBLIC TIMETABLE DATA</p><h2 id="schedule-title">{origin} → {destination}</h2></div><button type="button" className="link-button" onClick={() => setBusOptions(null)}>Close</button></div>
      {busSearchError && <div className="error-message" role="alert"><CircleAlert size={18}/><span>{busSearchError}</span></div>}
      {!busSearchError && busOptions.length === 0 && <p className="schedule-empty">No bus in this dataset lists both places in the same route. Try nearby stops or confirm with the local bus stand.</p>}
      {busOptions.map((option, i) => <article className="schedule-row" key={`${option.vehicle_number}-${option.trip_number ?? 'route'}-${i}`}>
        <span className="schedule-bus-icon"><BusFront size={18}/></span><div className="schedule-info"><strong>{option.vehicle_number}</strong><span>{option.stops.join(' → ')}</span><small>{option.timing_available ? <><Clock3 size={13}/> Departs {option.departure_time || 'time not listed'} · arrives {option.arrival_time || 'time not listed'}{option.trip_number ? ` · trip ${option.trip_number}` : ''}</> : 'Route is listed, but this stop pair has no extracted times.'}</small></div>
      </article>)}
      <div className="route-assist-actions"><button type="button" className="secondary-button" onClick={checkStopsWithGemini} disabled={routeAssistLoading || busLoading}><Sparkles size={16}/>{routeAssistLoading ? 'Gemini is checking the route…' : 'Ask Gemini to check major stops'}</button>{routeAssistError && <p className="assist-error" role="alert">{routeAssistError}</p>}</div>
      <GeminiRouteSuggestion routeAssist={routeAssist}/>
      {busSearchNotice && <p className="schedule-notice">{busSearchNotice}</p>}
      {busSearchNotice && <p className="schedule-notice">Route records are source-backed where listed, but may be incomplete and are not live timetable advice.</p>}{busOptions?.source && <p className="schedule-source">Source: {busOptions.source}</p>}
    </section>}

    {result && <section className={`result-card ${meta.tone}`} aria-labelledby="result-title" aria-live="polite">
      <div className="result-head"><span className="result-icon"><meta.Icon size={25}/></span><div><p className="kicker">{meta.label}</p><h2 id="result-title">{result.title}</h2></div><button type="button" className="icon-button" onClick={() => setResult(null)} aria-label="Dismiss result"><X size={19}/></button></div>
      <p className="result-message">{result.message}</p>
      {result.route_assist_error && <div className="error-message" role="status"><CircleAlert size={18}/><span>{result.route_assist_error}</span></div>}
      {result.route_assist && <GeminiRouteSuggestion routeAssist={result.route_assist}/>}
      {result.scan?.demo_mode && <p className="demo-recognition">DEMO MODE — simulated recognition. Gemini was not used.</p>}
      {result.scan?.recognition_uncertain && <p className="data-notice"><CircleAlert size={15}/>Gemini marked this transcription as unclear. Review the board text before relying on it.</p>}
      {result.scan?.notes && <p className="data-notice">Recognition note: {result.scan.notes}</p>}
      {result.scan?.user_confirmed && <p className="confirmed-note">User-confirmed destination — Gemini was not called again.</p>}
      <dl className="details-grid"><div><dt>Board text</dt><dd>{result.board_text_original || result.detected_destination || 'No clear destination found'}</dd></div><div><dt>Normalized board text</dt><dd>{result.board_text_normalized || '—'}</dd></div><div><dt>Your journey</dt><dd>{result.selected_origin || origin} → {result.selected_destination || destination}</dd></div>{result.normalized_destination && <div><dt>Known destination</dt><dd>{result.normalized_destination}</dd></div>}{result.scan?.original_transcription && result.scan.original_transcription !== result.detected_destination && <div><dt>Original recognition</dt><dd>{result.scan.original_transcription}</dd></div>}</dl>
      {result.matched_stops?.length > 0 && <div className="route-detail"><strong>Ordered stops in this route record</strong><span>{result.matched_stops.join(' → ')}</span></div>}
      {result.route_evidence && <div className="route-detail"><strong>Route evidence</strong><span>{result.route_evidence.route_name || result.route_evidence.operator || 'Source-backed route record'}{result.route_evidence.last_verified_at ? ` · last checked ${result.route_evidence.last_verified_at}` : ''}</span>{result.route_evidence.source_url && <a href={result.route_evidence.source_url} target="_blank" rel="noreferrer">View route source</a>}{result.route_evidence.current_confirmation_url && <a href={result.route_evidence.current_confirmation_url} target="_blank" rel="noreferrer">View latest operating-status report</a>}</div>}
      <div className="result-actions"><button type="button" className="secondary-button" onClick={readResult} disabled={speaking || speechLoading}><Volume2 size={17}/> {speechLoading ? 'Generating audio…' : speaking ? 'Reading aloud…' : 'Read result aloud'}</button>{(speaking || speechLoading) && <button type="button" className="secondary-button" onClick={stopSpeaking}><Square size={14}/> Stop speaking</button>}<button type="button" className="link-button review-link" onClick={() => { setReviewOpen(!reviewOpen); setReviewText(result.detected_destination || '') }}>Review board text</button></div>
      {voiceNotice && <p className="voice-note" role="status">{voiceNotice}</p>}
      {reviewOpen && <div className="manual-review"><label>Review or correct the text shown on the board<input value={reviewText} onChange={(e) => setReviewText(e.target.value)} /></label><p>Rechecks route data only. It does not make another Gemini request.</p><button className="primary-button compact" type="button" disabled={busy} onClick={confirmReview}>Confirm and recheck</button></div>}
      {result.alternatives?.length > 0 && <div className="alternatives"><h3>Available route options</h3><p>Ordered by fewer transfers, then fewer listed stops—not travel time.</p>{result.alternatives.map((option, i) => <div className="alternative-row" key={`${option.route_ids?.join('-')}-${i}`}><BusFront size={18}/><div><strong>{option.label}</strong><span>{option.stops.join(' → ')}</span></div><small>{option.stop_count} stops · {option.transfers} transfer{option.transfers === 1 ? '' : 's'}</small></div>)}</div>}
      {result.data_notice && <p className="data-notice"><CircleAlert size={15}/>{result.data_notice}</p>}
      <button className="link-button" type="button" onClick={resetScan}><RotateCcw size={15}/> Scan another bus</button>
    </section>}

    {routesNotice && <aside className="dataset-note"><Globe2 size={18}/><span>{routesNotice}</span></aside>}
    <footer><BusFront size={16}/><span>BusBoard Reader AI · A limited-route prototype, not live transit guidance.</span></footer>
  </main>
}
