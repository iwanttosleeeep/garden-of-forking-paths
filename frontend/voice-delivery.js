// Vendored from StackChan/tools/voice-lab; see docs/VOICE.md. Only transport is adapted.
// No credentials or recordings are stored in browser storage or sent by this file.
let deliveryReady = false, deliveryBusy = false, deliveryReceipt = null, deliverySnapshot = '';
function deliveryPayload() {
  const effective = effectiveEmotion();
  return {schema_version: 1, text: $('transcript').value.trim(),
    text_source: textTouched ? (transcription ? 'user_corrected' : 'user_entered') : 'user_confirmed_asr',
    transcription_source: transcription?.source || null,
    tone: {label: effective.label, source: effective.source, model_label: emotionResult?.emotion?.label || null,
      note: $('self-note').value || null},
    sound: {pitch_median_hz: report?.pitch_median_hz ?? null, pitch_coverage: report?.pitch_coverage ?? null,
      ending_pitch: report?.ending_pitch || 'unknown', rms_dbfs: report?.rms_dbfs ?? null,
      low_energy_gap_count: report?.low_energy_gaps?.length || 0, warnings: report?.warnings || []}};
}
function deliveryStateForCurrent() {
  if (!deliveryReceipt || deliveryReceipt.snapshot !== JSON.stringify(deliveryPayload())) return 'not_sent';
  if (Date.now() >= deliveryReceipt.expires) return 'expired_or_already_received';
  return deliveryReceipt.uncertain ? 'delivery_uncertain' : 'relay_uploaded_claude_receipt_unknown';
}
function deliveryControls() {
  const blocked = busy || deliveryBusy || transcribing || emotionBusy || !report || currentDemo;
  const samePending = deliveryReceipt && deliveryReceipt.snapshot === JSON.stringify(deliveryPayload()) && Date.now() < deliveryReceipt.expires;
  $('delivery-confirm').disabled = blocked || !deliveryReady || !!samePending;
  $('voice-send').disabled = blocked || !deliveryReady || !!samePending || !$('delivery-confirm').checked || !$('transcript').value.trim();
  $('voice-withdraw').disabled = deliveryBusy || !deliveryReceipt;
}
function deliveryPreview() {
  const snapshot = JSON.stringify(deliveryPayload());
  if (snapshot !== deliverySnapshot) $('delivery-confirm').checked = false;
  deliverySnapshot = snapshot;
  $('delivery-preview').textContent = JSON.stringify(deliveryPayload(), null, 2);
  deliveryControls();
}
async function deliveryStatus() {
  try {
    const response = await voiceFetch('/api/voice/status');
    const data = await response.json();
    deliveryReady = response.ok && data.configured === true && data.mode === 'latest-v1';
    $('delivery-config').textContent = deliveryReady ? `已配置目标：${data.destination} · 只保留最近一句，读后删除。` : '无领取码模式尚未完成服务更新；请暂勿发送，本地分析不受影响。';
  } catch {
    deliveryReady = false;
    $('delivery-config').textContent = '发送服务尚未就绪；如果刚更新过程序，请重启声音小实验。';
  }
  deliveryControls();
}
async function deliveryPost(operation, data) {
  const response = await voiceFetch('/api/voice/' + operation, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), signal: AbortSignal.timeout(18000)});
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || '服务器尚未确认操作，请先撤回后再试。');
  return result;
}
function renderReceipt() {
  $('delivery-receipt').hidden = !deliveryReceipt;
  deliveryControls();
}
document.addEventListener('DOMContentLoaded', () => {
  void deliveryStatus();
  $('delivery-confirm').addEventListener('change', deliveryControls);
  $('voice-send').addEventListener('click', async () => {
    if ($('voice-send').disabled || deliveryBusy) return;
    const payload = deliveryPayload();
    // Internal identifier only prevents an old withdrawal deleting a newer upload.
    // It is not a read credential and is never needed by Claude or the user.
    const id = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(24)))).replaceAll('+', '-').replaceAll('/', '_');
    deliveryReceipt = {id, snapshot: JSON.stringify(payload), uncertain: true, expires: Date.now() + 600000};
    deliveryBusy = true; controls(true); renderReceipt();
    $('delivery-status').textContent = '正在发送核对后的文字与声音摘要，原始录音留在本机…';
    try {
      await deliveryPost('publish', {confirmed: true, message_id: id, message: payload});
      deliveryReceipt.uncertain = false;
      $('delivery-status').textContent = `已发送。回到原来的 Claude 对话，说“听听我刚才说的”即可，让它调用 stackchan_receive_voice。无需领取码；消息约 ${new Date(deliveryReceipt.expires).toLocaleTimeString()} 到期，读取后立即删除。页面不确认是谁读了消息。`;
    } catch (error) {
      $('delivery-status').textContent = `未能确认送达：${error.message} 可先点“撤回本句”再试；新发送的一句也会替换旧消息。`;
    } finally {
      deliveryBusy = false; $('delivery-confirm').checked = false; controls(false); renderReceipt(); preview();
    }
  });
  $('voice-withdraw').addEventListener('click', async () => {
    if (!deliveryReceipt || deliveryBusy) return;
    deliveryBusy = true; deliveryControls();
    try {
      await deliveryPost('withdraw', {message_id: deliveryReceipt.id});
      deliveryReceipt = null; $('delivery-confirm').checked = false;
      message('本句若仍未读取，已从 VPS 删除；不会删除后来发送的新消息，也不能撤回 Claude 已收到的内容。');
    } catch (error) { $('delivery-status').textContent = `撤回未确认：${error.message} 消息仍会在上传后 10 分钟过期。`; }
    finally { deliveryBusy = false; renderReceipt(); preview(); }
  });
  setInterval(() => {
    if (deliveryReceipt && Date.now() >= deliveryReceipt.expires) {
      $('delivery-status').textContent = '本句已到期，或此前已被读取 / 替换。可以发送新的一句。';
      deliveryControls();
    }
  }, 1000);
});
