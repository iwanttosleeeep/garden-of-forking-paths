// Vendored from StackChan/tools/voice-lab; see docs/VOICE.md for transport/lifecycle adaptations.
const $ = id => document.getElementById(id);
let recorder, stream, clock, stopTimer, started, audioUrl;
let generation = 0, currentWav, report, transcription, capturedAt, currentSource, modelReady = false;
let busy = false, recording = false;
let currentDemo = false, transcribing = false, transcriptionRequest;
let monitorContext, monitorTimer, captureInfo = null;
let emotionResult = null, emotionReady = false, emotionBusy = false, emotionRequest;
let senseTranscription = null, whisperTranscription = null, textTouched = false;
let whisperError = '', senseError = '';

function invalidate() {
  generation++;
  $('delivery-confirm').checked = false;
  transcriptionRequest?.abort();
  transcriptionRequest = null; transcribing = false;
  emotionRequest?.abort(); emotionRequest = null; emotionBusy = false;
}
function resultControls() {
  $('retry').disabled = busy || transcribing || emotionBusy || currentDemo || !currentWav || !(modelReady || emotionReady);
  for (const id of ['export', 'clear', 'transcript', 'self-tone', 'self-note', 'emotion-reset']) $(id).disabled = busy || !report;
  $('emotion-retry').disabled = busy || emotionBusy || currentDemo || !currentWav || !emotionReady;
  deliveryControls();
}

function controls(locked) {
  busy = locked;
  $('file').disabled = locked;
  $('file').parentElement.classList.toggle('disabled', locked);
  $('demo').disabled = locked;
  $('record').disabled = locked && !recording;
  $('usb-probe').disabled = locked;
  $('usb-record').disabled = locked;
  for (const id of ['auto-stop', 'patience', 'sensitivity']) $(id).disabled = locked;
  resultControls();
}
function message(text) { $('status').textContent = text; }
function error(text) { $('error').textContent = text; $('error').hidden = !text; }
async function status() {
  try {
    const data = await (await voiceFetch('/api/status')).json();
    modelReady = data.transcription === 'ready';
    emotionReady = data.emotion === 'ready';
    $('emotion-model').textContent = emotionReady ? '本地 SenseVoice 已就绪' : data.emotion === 'loading' ? '本地标签模型加载中' : '自动标签未就绪 · 可手动标注';
    $('model').textContent = emotionReady ? 'SenseVoice 主转写已就绪' : modelReady ? 'Whisper 备用转写已就绪' : '转写未就绪 · 可手动填写';
  } catch { modelReady = false; emotionReady = false; $('model').textContent = '本地服务未连接'; $('emotion-model').textContent = '本地服务未连接'; }
  resultControls();
}
status();
setInterval(status, 5000);

async function usbOperation(recordAudio) {
  if (busy) return;
  if (recordAudio) invalidate();
  const token = generation;
  controls(true); error(''); $('player').pause();
  $('usb-status').textContent = recordAudio ? '正在准备收音；看到机器人显示 USB REC 后开口，5 秒后自动结束…' : '正在检查 USB 收音固件（不会开启麦克风）；首次连接可能重启，请等设备开机…';
  const request = new AbortController();
  const timeout = setTimeout(() => request.abort(), 70000);
  try {
    const response = await voiceFetch(recordAudio ? '/api/stackchan/record' : '/api/stackchan/probe', {method:'POST', signal:request.signal});
    if (!response.ok) {const data = await response.json();throw new Error(data.error || 'USB 操作失败');}
    if (token !== generation) return;
    if (!recordAudio) {
      const data = await response.json();
      $('usb-status').textContent = data.privacy ? 'USB 固件连接正常，目前为隐私模式，不能收音。' : 'StackChan 已连接！可以点“StackChan 录 5 秒”。';
    } else {
      const audio = await response.blob();
      if (token !== generation) return;
      capturedAt = null; // Device does not provide a synchronized recording timestamp.
      captureInfo = {mode:'stackchan_usb',duration_seconds:5,sample_rate:16000,channels:1,stop_reason:'limit'};
      $('usb-status').textContent = '5 秒录音已接收并校验，麦克风已关闭。';
      clearTimeout(timeout);
      await process(audio, 'StackChan USB 麦克风');
    }
  } catch(e) {
    if (token === generation) {
      const note = e.name === 'AbortError' ? 'USB 操作超时，请检查数据线和设备状态。设备收音最长 5 秒。' : e.message;
      $('usb-status').textContent = note; error(note);
    }
  } finally {
    clearTimeout(timeout);
    if (token === generation) controls(false);
  }
}
$('usb-probe').addEventListener('click', () => usbOperation(false));
$('usb-record').addEventListener('click', () => usbOperation(true));

function wave(samples, rate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
  const str = (at, text) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  str(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); str(8, 'WAVE');
  str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((x, i) => view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, x)) * (x < 0 ? 32768 : 32767)), true));
  return new Blob([buffer], {type: 'audio/wav'});
}
async function decode(blob) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (decoded.duration < .5 || decoded.duration > 30.25) throw new Error('请选取 0.5–30 秒的录音。');
    const offline = new OfflineAudioContext(1, Math.min(480000, Math.round(decoded.duration * 16000)), 16000);
    const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
    return wave((await offline.startRendering()).getChannelData(0));
  } finally { await context.close(); }
}
async function post(path, wav, signal) {
  const response = await voiceFetch(path, {method: 'POST', headers: {'Content-Type': 'audio/wav'}, body: wav, signal});
  const data = await response.json();
  if (!response.ok) {const failure = new Error(data.error || '处理失败，请重试。');failure.status = response.status;throw failure;}
  return data;
}
function updateTranscription() {
  transcription = senseTranscription || whisperTranscription;
  // User input is sticky, even if the user clears the field or retries recognition.
  if (!textTouched) $('transcript').value = transcription?.text || '';
  $('sense-text').textContent = senseTranscription?.text || (emotionBusy ? '正在识别…' : senseError || '暂无可用文字');
  $('whisper-text').textContent = whisperTranscription?.text || (transcribing ? '正在识别…' : whisperError || '暂无可用文字');
  if (currentDemo) return;
  let note;
  if (transcription) {
    note = senseTranscription ? '识别完成 · 主转写：SenseVoice。' : `识别完成 · 备用转写：Whisper。${emotionBusy ? '正在等待 SenseVoice 主转写。' : 'SenseVoice 暂无可用文字。'}`;
    if (senseTranscription && transcribing) note += 'Whisper 对照仍在识别中。';
    note += textTouched ? '已保留你手动修改的文字。' : '请回放核对，尤其是专有名词。';
  } else if (emotionBusy || transcribing) note = '正在本机转写，请稍等…';
  else note = '暂无可用转写，可重试或手动填写。展开下方对照可查看状态。';
  $('transcript-status').textContent = note;
}
async function transcribe(token = generation) {
  if (!currentWav || currentDemo || transcribing || !modelReady) return;
  transcribing = true; resultControls();
  const request = new AbortController(); transcriptionRequest = request;
  whisperError = ''; updateTranscription();
  try {
    const wav = currentWav;
    let data;
    for (let attempt = 0; attempt < 9; attempt++) {
      if (token !== generation) return;
      try { data = await post('/api/transcribe', wav, request.signal); break; }
      catch(e) {
        if (e.status !== 429 || attempt === 8) throw e;
        if (token !== generation) return;
        await new Promise(resolve=>setTimeout(resolve, 1000));
      }
    }
    if (token !== generation) return;
    whisperTranscription = data.text?.trim() ? {...data, source: 'whisper'} : null;
    if (!whisperTranscription) whisperError = '没有识别出清晰语音';
  } catch (e) { if (token === generation) whisperError = e.message; }
  finally { if (token === generation) {transcribing = false; updateTranscription(); resultControls(); preview();} }
}
async function process(blob, name, demo = false) {
  invalidate(); const token = generation;
  controls(true); error(''); message('正在读取声音…');
  $('player').pause();
  report = null; transcription = null; currentWav = null; currentDemo = demo;
  senseTranscription = whisperTranscription = null; textTouched = false;
  senseError = whisperError = ''; $('transcript').value = ''; updateTranscription();
  $('self-tone').value = ''; $('self-note').value = '';
  emotionResult = null; renderEmotion();
  $('result').hidden = true; $('charts').hidden = true; $('empty').hidden = false;
  try {
    const wav = await decode(blob);
    if (token !== generation) return;
    currentWav = wav; currentSource = name;
    message('正在分析音高与声音轻重…');
    const result = await post('/api/analyze', wav);
    if (token !== generation) return;
    report = result;
    preview();
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioUrl = URL.createObjectURL(wav); $('player').src = audioUrl;
    $('source').textContent = `${name} · ${result.duration_seconds.toFixed(1)} 秒`;
    $('transcript').value = '';
    preview();
    $('pitch').textContent = result.pitch_median_hz === null ? '无法估计' : `${Math.round(result.pitch_median_hz)} Hz`;
    $('pitch-range').textContent = result.pitch_range_hz ? `主要范围 ${result.pitch_range_hz.map(Math.round).join('–')} Hz` : '稳定周期信号不足';
    $('level').textContent = `${result.rms_dbfs} dBFS`;
    $('gaps').textContent = `${result.low_energy_gaps.length} 处`;
    $('summary').textContent = result.summary;
    $('warnings').textContent = result.warnings.join(' ');
    $('result').hidden = false; $('empty').hidden = true; $('charts').hidden = false;
    draw();
    message(demo ? '模拟音频：两个音高不同的音调，中间留有空白。' : '声音分析完成。可以回放，再换个语气试试。');
    if (demo) {
      $('emotion-status').textContent = '这是模拟音调，不做人声情绪识别。';
      $('transcript-status').textContent = '这是合成音调，没有说话内容，不做文字转写。';
      $('retry').disabled = true;
    } else {
      if (emotionReady) void estimateEmotion(token);
      else $('emotion-status').textContent = '本地自动标签未就绪，可以先手动补充，稍后重试。';
      $('retry').disabled = false;
      if (modelReady) void transcribe(token);
      if (!emotionReady) senseError = 'SenseVoice 未就绪，可稍后重新转写';
      if (!modelReady) whisperError = 'Whisper 未就绪，可稍后重新转写';
      updateTranscription();
    }
  } catch(e) {
    error(e.name === 'EncodingError' ? '浏览器无法解码这段录音。请导出为 WAV、MP3 或 M4A 再试。' : e.message);
    message('这次没有完成，可以再试一次。');
  } finally { if (token === generation) controls(false); }
}

function releaseMic() {
  clearInterval(clock); clearTimeout(stopTimer);
  clearInterval(monitorTimer);
  if (monitorContext) { void monitorContext.close().catch(()=>{}); monitorContext = null; }
  $('mic-level').value = -80;
  stream?.getTracks().forEach(t => t.stop()); stream = null;
  recording = false;
  $('orb').classList.remove('active'); $('record').classList.remove('recording');
  $('record').textContent = '再录一句';
}
function stop(reason = 'manual') {
  if (recorder?.state !== 'recording') return;
  recorder.capture.stop_reason = reason;
  recorder.stop(); releaseMic();
  $('listening').textContent = reason === 'silence' ? '已等待你的停顿，录音自动结束。' : reason === 'limit' ? '已到 30 秒上限，录音结束。' : '麦克风已关闭。';
}
function monitor(analyser, auto) {
  const profiles = {normal: [.02, .008], soft: [.009, .004], noisy: [.04, .016]};
  const [start, continuing] = profiles[$('sensitivity').value];
  const detector = new VoiceEndpoint({silenceMs: Number($('patience').value), start, continuing});
  const samples = new Float32Array(analyser.fftSize);
  monitorTimer = setInterval(() => {
    if (!recording) return;
    analyser.getFloatTimeDomainData(samples);
    const mean = samples.reduce((a,b)=>a+b,0) / samples.length;
    const rms = Math.sqrt(samples.reduce((a,b)=>a+(b-mean)**2,0) / samples.length);
    $('mic-level').value = Math.max(-80, 20 * Math.log10(Math.max(rms, .0001)));
    if (!auto) return;
    const state = detector.update(rms, performance.now());
    $('listening').textContent = !state.heard ? '正在等你开口，录音已经开始。' : state.quietMs > 300 ? '我在等你说完，可以接着说…' : '正在听，请慢慢说。';
    if (state.stop) stop('silence');
  }, 50);
}
$('record').addEventListener('click', async () => {
  if (recording) return stop();
  if (busy) return;
  invalidate(); const captureToken = generation;
  controls(true); error(''); $('player').pause();
  if (report) $('transcript-status').textContent = '上一段结果；开始新录音后不再接收上一段转写。';
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前浏览器不支持录音。请用 Chrome 打开，或上传手机录音。');
    message('请允许浏览器使用麦克风…');
    const acquired = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, autoGainControl: false, noiseSuppression: false}});
    if (captureToken !== generation) {acquired.getTracks().forEach(t=>t.stop()); return;}
    stream = acquired;
    monitorContext = new AudioContext();
    await monitorContext.resume();
    if (captureToken !== generation) return;
    const analyser = monitorContext.createAnalyser(); analyser.fftSize = 2048;
    monitorContext.createMediaStreamSource(stream).connect(analyser);
    const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(t => MediaRecorder.isTypeSupported(t));
    recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : {});
    const chunks = [];
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    const activeRecorder = recorder;
    recorder.onstop = () => {
      const blob = new Blob(chunks, {type: activeRecorder.mimeType});
      captureInfo = activeRecorder.capture; capturedAt = activeRecorder.capturedAt;
      recorder = null; releaseMic(); void process(blob, '麦克风录音');
    };
    recorder.onerror = () => { recorder.onstop = null; releaseMic(); controls(false); error('录音中断，请重新录制。'); };
    recorder.start(); recording = true; started = performance.now();
    recorder.capturedAt = new Date().toISOString();
    recorder.capture = {mode: $('auto-stop').checked ? 'energy_endpoint' : 'manual', silence_ms: Number($('patience').value), sensitivity: $('sensitivity').value, stop_reason: null};
    monitor(analyser, $('auto-stop').checked);
    $('listening').textContent = '录音已开始，开头和停顿都会保留。';
    $('record').disabled = false; $('record').textContent = '停止并分析'; $('record').classList.add('recording'); $('orb').classList.add('active');
    message('正在录音。说完点“停止并分析”，最长 30 秒。');
    const tick = () => { const seconds = Math.min(30, Math.floor((performance.now() - started) / 1000)); $('timer').innerHTML = `00:${String(seconds).padStart(2,'0')} <span>/ 00:30</span>`; };
    tick(); clock = setInterval(tick, 200); stopTimer = setTimeout(()=>stop('limit'), 30000);
  } catch(e) { releaseMic(); controls(false); error(e.name === 'NotAllowedError' ? '麦克风权限未开启。请在浏览器地址栏允许麦克风，或使用上传录音。' : e.message); message('还没有开始录音。'); }
});
$('file').addEventListener('change', e => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return;
  if (file.size > 20 * 1024 * 1024) return error('文件大于 20 MB，请裁剪到 30 秒以内再上传。');
  capturedAt = null; captureInfo = null; void process(file, file.name);
});
$('retry').addEventListener('click', () => { void estimateEmotion(); void transcribe(); });
$('demo').addEventListener('click', () => {
  const s = new Float32Array(16000 * 4);
  for(let i=0;i<s.length;i++) { const t=i/16000; if(t>.2 && t<1.6 || t>2.2 && t<3.8) {const hz=t<2?160:240; s[i]=.15*Math.sin(2*Math.PI*hz*t);} }
  capturedAt = null; captureInfo = null; void process(wave(s), '模拟音频（不是人声）', true);
});
function voiceContext() {
  return {text: $('transcript').value, text_source: textTouched ? (transcription ? 'user_edited' : 'user_entered') : transcription ? 'local_asr_unverified' : 'unavailable',
    transcription_source: transcription?.source || null,
    measured: report ? {pitch_median_hz: report.pitch_median_hz, ending_pitch: report.ending_pitch, rms_dbfs: report.rms_dbfs, low_energy_gaps: report.low_energy_gaps, warnings: report.warnings} : null,
    user_annotation: {tone: $('self-tone').value || null, note: $('self-note').value || null},
    model_emotion: emotionResult, effective_emotion: effectiveEmotion(),
    caveat: '用户修正优先；保留模型原始结果。模型仅估计声音标签，不据声音断言真实心情。', delivery: deliveryStateForCurrent()};
}
function effectiveEmotion() {
  const choice = $('self-tone').value;
  if (choice === '__reject__') return {label: null, source: 'user_rejected'};
  if (choice) return {label: choice, source: 'user_correction'};
  const label = emotionResult?.emotion?.label || null;
  return {label, source: label ? 'model_estimate' : 'unavailable'};
}
function renderEmotion() {
  $('auto-emotion').textContent = emotionResult ? emotionResult.emotion?.label || '无法判断' : '尚未分析';
  $('auto-events').textContent = emotionResult?.events?.length ? `模型声音事件：${emotionResult.events.map(e=>e.label).join('、')}` : '';
  const effective = effectiveEmotion();
  const source = {user_rejected: '你选择了不采用自动标签', user_correction: '以你的补充 / 修正为准', model_estimate: '模型估计，未经你确认', unavailable: '暂无可用标签'}[effective.source];
  $('effective-emotion').textContent = `当前采用：${effective.label || '不判断'} · ${source}`;
}
async function estimateEmotion(token = generation) {
  if (!currentWav || currentDemo || emotionBusy || !emotionReady) return;
  emotionBusy = true; resultControls();
  senseError = ''; updateTranscription();
  const request = new AbortController(); emotionRequest = request;
  const wav = currentWav;
  $('emotion-status').textContent = '正在本机分析声音标签…';
  try {
    let result;
    for(let attempt=0;attempt<9;attempt++) {
      if(token!==generation)return;
      try {result=await post('/api/emotion',wav,request.signal);break;}
      catch(e) {if(e.status!==429 || attempt===8)throw e;await new Promise(resolve=>setTimeout(resolve,1000));}
    }
    if(token!==generation)return;
    emotionResult = result;
    // Emotion may be unknown while the transcript is still usable. Non-speech is not text.
    const speech = result.events?.some(event => ['Speech', 'Speech_Noise'].includes(event.code));
    senseTranscription = speech && result.raw?.text?.trim() ? {
      text: result.raw.text, engine: result.engine, source: 'sensevoice',
      language: (result.raw.language || '').replace(/^<\||\|>$/g, '') || null,
      segments: null
    } : null;
    if (!senseTranscription) senseError = result.reason || '没有识别出清晰语音';
    $('emotion-status').textContent = result.reason || '自动分析完成。可以保留，也可以在下方修正。';
  } catch(e) {if(token===generation){senseError=e.message;$('emotion-status').textContent = `${e.message}${emotionResult ? ' 保留上次自动结果。' : ''}`;}}
  finally {if(token===generation){emotionBusy=false;updateTranscription();resultControls();preview();}}
}
$('emotion-retry').addEventListener('click',()=>estimateEmotion());
$('emotion-reset').addEventListener('click',()=>{$('self-tone').value='';preview();});
function preview() { renderEmotion(); $('context-preview').textContent = JSON.stringify(voiceContext(), null, 2); deliveryPreview(); }
$('transcript').addEventListener('input', () => {textTouched = true; updateTranscription(); preview();});
for (const id of ['self-tone', 'self-note']) $(id).addEventListener('input', preview);
$('clear').addEventListener('click', () => {
  invalidate(); $('player').pause(); $('player').removeAttribute('src'); $('player').load();
  if (audioUrl) URL.revokeObjectURL(audioUrl); audioUrl = null;
  currentWav = report = transcription = captureInfo = capturedAt = emotionResult = null;
  senseTranscription = whisperTranscription = null; textTouched = false;
  senseError = whisperError = ''; updateTranscription();
  for (const id of ['transcript', 'self-tone', 'self-note']) $(id).value = '';
  renderEmotion(); $('emotion-status').textContent = '录音后自动分析。';
  $('result').hidden = $('charts').hidden = true; $('empty').hidden = false;
  $('context-preview').textContent = ''; error(''); message('本次录音与标注已从页面清空；不会删除你主动导出的文件。'); controls(false);
});
$('export').addEventListener('click', () => {
  if (!report) return;
  const body = {version: 4, analyzed_at: new Date().toISOString(), recorded_at: capturedAt, source: currentSource,
    text: $('transcript').value, text_edited: textTouched, transcription,
    transcription_candidates: {sensevoice: senseTranscription, whisper: whisperTranscription},
    transcription_status: {sensevoice: emotionBusy ? 'running' : senseError || (senseTranscription ? 'ready' : 'unavailable'), whisper: transcribing ? 'running' : whisperError || (whisperTranscription ? 'ready' : 'unavailable')},
    acoustics: report, capture: captureInfo, voice_context: voiceContext()};
  const url = URL.createObjectURL(new Blob([JSON.stringify(body,null,2)], {type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'stackchan-voice-result.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
});
function chart(id, series, key, min, max, color) {
  const canvas=$(id), width=canvas.clientWidth, height=canvas.clientHeight, dpr=devicePixelRatio||1;
  canvas.width=width*dpr;canvas.height=height*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);
  const left=43,right=width-14,top=12,bottom=height-28;
  const x=t=>left+t/report.duration_seconds*(right-left), y=v=>bottom-(v-min)/(max-min)*(bottom-top);
  c.font='11px -apple-system, sans-serif';c.lineWidth=1;
  for(let i=0;i<4;i++){const v=min+(max-min)*i/3, yy=y(v);c.strokeStyle='#e5e9e0';c.beginPath();c.moveTo(left,yy);c.lineTo(right,yy);c.stroke();c.fillStyle='#758177';c.textAlign='right';c.fillText(Math.round(v),left-7,yy+4);}
  c.textAlign='center'; for(let i=0;i<5;i++){const t=report.duration_seconds*i/4;c.fillText(`${t.toFixed(1)}s`,x(t),height-8);}
  if(key==='dbfs'){c.fillStyle='#efd8c55c';for(const gap of report.low_energy_gaps)c.fillRect(x(gap.start),top,x(gap.end)-x(gap.start),bottom-top);}
  c.save();c.beginPath();c.rect(left,top,right-left,bottom-top);c.clip();c.strokeStyle=color;c.lineWidth=1.8;c.beginPath();let active=false;
  for(const p of series){if(p[key]===null){active=false;continue;}if(active)c.lineTo(x(p.time),y(p[key]));else{c.moveTo(x(p.time),y(p[key]));active=true;}}c.stroke();c.restore();
}
function draw(){if(!report)return;const values=report.pitch_series.map(p=>p.hz).filter(p=>p!==null);const min=values.length?Math.max(0,Math.min(...values)-30):50,max=values.length?Math.max(...values)+30:350;chart('pitch-chart',report.pitch_series,'hz',min,max,'#52745f');chart('energy-chart',report.energy_series,'dbfs',-100,0,'#b58765');}
new ResizeObserver(draw).observe($('charts'));
// A lost bridge must not leave a live microphone behind a disabled fieldset.
// invalidate also rejects a getUserMedia grant that arrives after disconnection.
function voiceDisconnected() {
  invalidate();
  if (recorder?.state === 'recording') { recorder.onstop = null; recorder.stop(); }
  recorder = null;
  releaseMic();
  controls(false);
  message('本机连接中断，麦克风已关闭。本页不会自动重录或重发，请连接后再试。');
}
window.addEventListener('pagehide',()=>{invalidate();if(recorder?.state==='recording'){recorder.onstop=null;recorder.stop();}releaseMic();});
