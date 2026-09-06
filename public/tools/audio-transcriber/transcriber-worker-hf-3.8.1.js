import { env, pipeline } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1';

// The models are fetched from Hugging Face only after the page sends LOAD_MODEL.
env.allowLocalModels = false;
env.allowRemoteModels = true;
env.useBrowserCache = true;

// Transformers.js v3 ships its own matching ONNX Runtime Web build; let it
// resolve the wasm binaries itself instead of pointing at the old 2.6.2 path.
env.backends.onnx.wasm.numThreads = 1;

self.onerror = (e) => {
    console.error('Worker error:', e);
    self.postMessage({
        type: 'ERROR',
        payload: { stage: 'worker', message: e.message || 'The speech worker could not start.' }
    });
};

let transcriber = null;
let currentModelName = null;

self.addEventListener('message', async (event) => {
    const { type, payload } = event.data;

    if (type === 'LOAD_MODEL') {
        const { modelName } = payload;
        if (transcriber && currentModelName === modelName) {
            self.postMessage({ type: 'MODEL_READY' });
            return;
        }
        try {
            transcriber = await pipeline('automatic-speech-recognition', modelName, {
                // v3 replaced the boolean `quantized` flag with `dtype`.
                // "q8" is the closest equivalent to the old quantized=true behavior.
                dtype: 'q8',
                device: 'wasm',
                progress_callback: (data) => {
                    self.postMessage({ type: 'DOWNLOAD_PROGRESS', payload: data });
                }
            });
            currentModelName = modelName;
            self.postMessage({ type: 'MODEL_READY' });
        } catch (err) {
            transcriber = null;
            currentModelName = null;
            self.postMessage({
                type: 'ERROR',
                payload: { stage: 'model', message: err.message || String(err) }
            });
        }
    }

    if (type === 'TRANSCRIBE') {
        if (!transcriber) {
            self.postMessage({
                type: 'ERROR',
                payload: { stage: 'model', message: 'Initialize the speech engine before transcribing audio.' }
            });
            return;
        }
        const { audio, sampleRate } = payload;
        try {
            const result = await transcriber(audio, {
                sampling_rate: sampleRate,
                chunk_length_s: 30,
                stride_length_s: 5,
                max_new_tokens: 440
            });
            self.postMessage({ type: 'TRANSCRIPT_DONE', payload: result.text });
        } catch (err) {
            self.postMessage({
                type: 'ERROR',
                payload: { stage: 'transcription', message: err.message || String(err) }
            });
        }
    }
});
