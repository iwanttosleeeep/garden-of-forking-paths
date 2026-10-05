/* Fixed loopback transport. Never forwards Garden cookies or accepts a URL override. */
const GardenVoice = (() => {
  const origin = 'http://127.0.0.1:8765';
  const paths = new Set(['/api/status', '/api/voice/status', '/api/stackchan/status',
    '/api/analyze', '/api/transcribe', '/api/emotion', '/api/stackchan/probe',
    '/api/stackchan/record', '/api/voice/publish', '/api/voice/withdraw']);
  let connected = false;
  function show(ready, note) {
    if (connected && !ready && typeof voiceDisconnected === 'function') voiceDisconnected();
    connected = ready;
    document.getElementById('voice-workspace').disabled = !ready;
    document.getElementById('bridge-status').textContent = note;
    document.getElementById('bridge-title').textContent = ready ? '本机通道已接通。' : '先接通这台电脑。';
    document.getElementById('bridge-connect').hidden = ready;
    document.querySelector('.connection').classList.toggle('connected', ready);
  }
  async function request(path, options = {}) {
    if (!paths.has(path)) throw new Error('不是声音小屋允许的接口。');
    const headers = new Headers(options.headers);
    headers.set('X-Garden-Voice', '1');
    return fetch(origin + path, {...options, headers, mode: 'cors', credentials: 'omit',
      cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
      signal: options.signal || AbortSignal.timeout(8000)});
  }
  async function api(path, options) {
    if (!connected) throw new Error('请先点击“连接这台电脑”。');
    try { return await request(path, options); }
    catch (error) {
      // A cancelled recognition is not a lost connection. Do not replay any POST.
      if (error instanceof TypeError || (!options?.signal && error.name === 'TimeoutError')) {
        show(false, '本机连接已中断。请确认服务仍在运行，然后重新连接；本页不会自动重发。');
      }
      throw error;
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('bridge-connect');
    button.addEventListener('click', async () => {
      button.disabled = true;
      document.getElementById('bridge-status').textContent = '正在连接；若浏览器询问本地网络权限，请选择允许。';
      try {
        // Give the user time to answer the browser's first local-network prompt.
        const response = await request('/api/status', {signal: AbortSignal.timeout(30000)});
        const data = await response.json();
        if (!response.ok || data.app !== 'stackchan-voice-lab' || data.bridge_protocol !== 'garden-voice-v1') {
          throw new Error('本机服务版本不匹配，请更新并重启声音服务。');
        }
        show(true, 'USB 收音与识别都在这台电脑进行。准备好后，点下方录音按钮；模型状态会自动更新。');
        document.getElementById('bridge-help').open = false;
        await status();
        await deliveryStatus();
      } catch (error) {
        show(false, error instanceof TypeError || error.name === 'TimeoutError'
          ? '暂时没有接通。请确认声音服务已启动，并允许 Garden 的本地网络访问权限。' : error.message);
        document.getElementById('bridge-help').open = true;
      } finally { button.disabled = false; }
    });
  });
  return {api};
})();
function voiceFetch(path, options) { return GardenVoice.api(path, options); }
