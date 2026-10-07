'use strict';

let token = new URLSearchParams(location.hash.slice(1)).get('token');
try {
  if (token) sessionStorage.setItem('nano-shell-token', token);
  else token = sessionStorage.getItem('nano-shell-token');
} catch (_) { /* A disabled session store still permits the initial setup window. */ }
history.replaceState(null, '', location.pathname);
const statusNode = document.getElementById('status');
const button = document.getElementById('initialize');
const progress = document.getElementById('download');
const options = {
  expectedInputs: [{ type: 'text', languages: ['en'] }],
  expectedOutputs: [{ type: 'text', languages: ['en'] }],
};
let model = null;
let state = 'unavailable';
let detail = '';
let connected = true;

async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(5000),
    cache: 'no-store',
    credentials: 'omit',
  });
  if (response.status === 204) return null;
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Bridge returned ${response.status}`);
  return result;
}

async function report(next, message) {
  state = next;
  detail = message;
  statusNode.textContent = message;
  try {
    await request('/worker/status', { state, detail });
    connected = true;
  } catch (error) {
    connected = false;
    statusNode.textContent = `Bridge connection failed: ${error.message}`;
  }
}

async function checkAvailability() {
  if (!token) {
    statusNode.textContent = 'Missing bridge credentials. Open this window with nano-shell setup.';
    return;
  }
  if (!('LanguageModel' in self)) {
    await report('unavailable', 'LanguageModel is unavailable in this Chrome profile. Check Chrome built-in AI support and Prompt API availability.');
    return;
  }
  try {
    const availability = await LanguageModel.availability(options);
    const messages = {
      unavailable: 'Gemini Nano is unavailable on this device or Chrome profile.',
      downloadable: 'Gemini Nano needs a download. Click Initialize to begin.',
      downloading: 'Gemini Nano is downloading. Click Initialize to attach to the download.',
      available: 'Gemini Nano is available. Click Initialize to connect this worker.',
    };
    await report(availability, messages[availability] || `Unknown availability: ${availability}`);
    button.disabled = availability === 'unavailable' || !messages[availability];
  } catch (error) {
    await report('error', `Availability check failed: ${error.message}`);
  }
}

button.addEventListener('click', async () => {
  button.disabled = true;
  // Call create while the button click still supplies transient user activation.
  try {
    const creation = LanguageModel.create({
      ...options,
      monitor(monitor) {
        monitor.addEventListener('downloadprogress', (event) => {
          progress.hidden = false;
          progress.value = event.loaded;
          void report('downloading', `Downloading Gemini Nano: ${Math.round(event.loaded * 100)}%`);
        });
      },
    }).then(
      (value) => ({ value }),
      (error) => ({ error }),
    );
    await report('initializing', 'Initializing Gemini Nano…');
    const settled = await creation;
    if (settled.error) throw settled.error;
    model = settled.value;
    progress.hidden = true;
    await report('ready', 'Gemini Nano is ready. Keep this window open.');
  } catch (error) {
    model = null;
    button.disabled = false;
    await report('error', `Initialization failed: ${error.message}`);
  }
});

async function poll() {
  while (token) {
    try {
      if (model && state === 'ready') {
        const job = await request('/worker/poll');
        if (job) {
          let session;
          try {
            session = await model.clone();
            const text = await session.prompt(job.prompt, { signal: AbortSignal.timeout(30000) });
            await request('/worker/result', { id: job.id, text });
          } catch (error) {
            await request('/worker/result', { id: job.id, error: String(error.message).slice(0, 2048) });
            statusNode.textContent = `Last inference failed: ${error.message}`;
          } finally {
            if (session) session.destroy();
          }
        }
      } else {
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (error) {
      connected = false;
      statusNode.textContent = `Bridge connection failed: ${error.message}`;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}

setInterval(() => {
  if (token) void report(state, connected ? detail : `Reconnecting: ${detail}`);
}, 5000);
void checkAvailability();
void poll();
