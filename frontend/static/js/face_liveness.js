/**
 * Netra-I — client side of the face liveness protocol.
 *
 * This module drives the camera and the on-screen prompts. It deliberately does
 * NOT decide whether the person is live or who they are: it collects one frame
 * plus one descriptor per challenge step and posts them. Every verdict —
 * liveness, head movement, identity — is computed on the server, because a
 * client-side verdict would be forgeable with a single crafted request.
 *
 * Usage:
 *   const lv = new NetraFaceLiveness({ video, onPrompt, onProgress });
 *   const payload = await lv.runChallenge('login');   // -> {nonce, steps}
 *   // POST payload to /face/login, /face/enroll or /face/verify-exam
 */
(function (global) {
  'use strict';

  /* Served locally (frontend/static/vendor) so face checks work offline.
     base.html sets the exact URLs; these defaults match the default static path. */
  const MODEL_URL = (global.NETRA_FACE_MODEL_URL || '/static/vendor/face-api/models');
  const FACEAPI_SRC = (global.NETRA_FACEAPI_SRC || '/static/vendor/face-api/face-api.min.js');

  /* Frames are sent to the server for texture analysis, so they need more
     resolution than the 320x240 used for routine proctoring — moire and
     specular artefacts vanish at low resolution. */
  const CAPTURE_W = 480;
  const CAPTURE_H = 360;
  const JPEG_QUALITY = 0.88;

  const HOLD_MS = 350;          // how long a pose must be held before capture
  const STEP_TIMEOUT_MS = 15000; // give up on one step after this
  const POLL_MS = 220;

  let modelsReady = false;

  function loadScript(src) {
    return new Promise(resolve => {
      const el = document.createElement('script');
      el.src = src;
      el.onload = () => resolve(true);
      el.onerror = () => resolve(false);
      document.head.appendChild(el);
    });
  }

  async function ensureModels() {
    if (modelsReady) return true;
    if (!global.faceapi && !(await loadScript(FACEAPI_SRC))) return false;
    if (!global.faceapi) return false;
    try {
      await Promise.all([
        faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
        faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODEL_URL),
        faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL)
      ]);
      modelsReady = true;
      return true;
    } catch (e) {
      console.warn('[liveness] model load failed', e);
      return false;
    }
  }

  /** Rough yaw/pitch from 68-point landmarks.
   *  Used ONLY to tell the user "keep going" while they move — the server
   *  re-measures pose with its own model and that is what actually counts. */
  function coarsePose(landmarks) {
    try {
      const pts = landmarks.positions;
      const leftEye = pts[36], rightEye = pts[45], nose = pts[30];
      const chin = pts[8], brow = pts[27];

      const eyeMidX = (leftEye.x + rightEye.x) / 2;
      const eyeSpan = Math.abs(rightEye.x - leftEye.x) || 1;
      const yaw = ((nose.x - eyeMidX) / eyeSpan) * 90;

      const faceH = Math.abs(chin.y - brow.y) || 1;
      const pitch = ((nose.y - (brow.y + chin.y) / 2) / faceH) * 90;

      return { yaw, pitch };
    } catch (e) {
      return { yaw: 0, pitch: 0 };
    }
  }

  class NetraFaceLiveness {
    /**
     * @param {object} opts
     * @param {HTMLVideoElement} opts.video   where the camera preview renders
     * @param {function} [opts.onPrompt]      (text, stepIndex, totalSteps)
     * @param {function} [opts.onProgress]    (doneCount, totalSteps)
     * @param {function} [opts.onError]       (message)
     */
    constructor(opts) {
      this.video = opts.video;
      this.onPrompt = opts.onPrompt || function () {};
      this.onProgress = opts.onProgress || function () {};
      this.onError = opts.onError || function () {};
      this.stream = null;
      this.cancelled = false;

      this.canvas = document.createElement('canvas');
      this.canvas.width = CAPTURE_W;
      this.canvas.height = CAPTURE_H;
      this.ctx = this.canvas.getContext('2d');
    }

    async startCamera() {
      /* The exam page hands us its already-running preview. Opening a second
         getUserMedia on the same device would replace that stream, and stopping
         it afterwards would kill the exam's own monitoring camera — so if a live
         stream is already attached, borrow it and leave ownership alone. */
      const existing = this.video.srcObject;
      if (existing && existing.getVideoTracks().some(t => t.readyState === 'live')) {
        this.borrowed = true;
        await this.video.play().catch(() => {});
        return;
      }

      this.borrowed = false;
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: CAPTURE_W, height: CAPTURE_H, facingMode: 'user' },
        audio: false
      });
      this.video.srcObject = this.stream;
      await this.video.play().catch(() => {});
    }

    stopCamera() {
      if (this.borrowed) return;        // not ours to stop
      if (this.stream) {
        this.stream.getTracks().forEach(t => t.stop());
        this.stream = null;
      }
    }

    cancel() {
      this.cancelled = true;
      this.stopCamera();
    }

    grabFrame() {
      /* Draw unmirrored: the preview is CSS-mirrored for the user's comfort,
         but the server must analyse the true image. Preserve aspect ratio so a
         borrowed stream of a different size is not squashed. */
      const vw = this.video.videoWidth || CAPTURE_W;
      const vh = this.video.videoHeight || CAPTURE_H;
      const scale = Math.min(CAPTURE_W / vw, CAPTURE_H / vh);
      const dw = Math.round(vw * scale), dh = Math.round(vh * scale);

      if (this.canvas.width !== dw || this.canvas.height !== dh) {
        this.canvas.width = dw;
        this.canvas.height = dh;
      }
      this.ctx.drawImage(this.video, 0, 0, dw, dh);
      return this.canvas.toDataURL('image/jpeg', JPEG_QUALITY);
    }

    async detect() {
      const opts = new faceapi.TinyFaceDetectorOptions({
        inputSize: 288, scoreThreshold: 0.5
      });
      try {
        return await faceapi.detectSingleFace(this.video, opts)
          .withFaceLandmarks(true).withFaceDescriptor();
      } catch (e) {
        return null;
      }
    }

    /** Ask the server for a challenge, walk the user through it, return the payload. */
    async runChallenge(purpose, challengeUrl) {
      this.cancelled = false;

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('This browser cannot use the camera.');
      }

      this.onPrompt('Loading face model…', 0, 0);
      if (!(await ensureModels())) {
        throw new Error('Could not load the face model. Check your connection.');
      }

      const res = await fetch(challengeUrl || '/face/challenge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ purpose })
      });
      const challenge = await res.json();
      if (!challenge.success) {
        throw new Error(challenge.message || 'Could not start the liveness check.');
      }

      const total = challenge.steps.length;
      await this.startCamera();

      const steps = [];
      let reference = null;    // coarse pose captured at the 'center' step

      try {
        for (let i = 0; i < total; i++) {
          if (this.cancelled) throw new Error('cancelled');

          const step = challenge.steps[i];
          this.onPrompt(step.prompt, i, total);

          const captured = await this._captureStep(step.action, reference, i, total);
          if (step.action === 'center') reference = captured.pose;

          steps.push({
            action: step.action,
            frame: captured.frame,
            descriptor: captured.descriptor,
            t: captured.t
          });

          this.onProgress(steps.length, total);

          /* A brief pause between steps keeps the frames genuinely distinct and
             gives the user time to read the next instruction. */
          if (i < total - 1) await new Promise(r => setTimeout(r, 420));
        }
      } finally {
        this.stopCamera();
      }

      return { nonce: challenge.nonce, steps: steps, deep_pad: challenge.deep_pad };
    }

    /** Hold until the requested movement is visibly underway, then capture. */
    async _captureStep(action, reference, index, total) {
      const started = Date.now();
      let heldSince = null;
      let lastGood = null;
      let noFaceTicks = 0;

      while (Date.now() - started < STEP_TIMEOUT_MS) {
        if (this.cancelled) throw new Error('cancelled');
        await new Promise(r => setTimeout(r, POLL_MS));

        if (this.video.readyState < 2 || !this.video.videoWidth) continue;

        const det = await this.detect();
        if (!det || !det.descriptor) {
          heldSince = null;
          if (++noFaceTicks % 8 === 0) {
            this.onPrompt('No face detected — move into the frame and check the light.',
                          index, total);
          }
          continue;
        }
        noFaceTicks = 0;

        const pose = coarsePose(det.landmarks);
        lastGood = { det, pose };

        if (this._satisfies(action, pose, reference)) {
          if (heldSince === null) heldSince = Date.now();
          if (Date.now() - heldSince >= HOLD_MS) {
            return {
              frame: this.grabFrame(),
              descriptor: Array.from(det.descriptor),
              pose: pose,
              t: Date.now()
            };
          }
        } else {
          heldSince = null;
        }
      }

      /* Timed out. Send the best frame we saw so the SERVER can rule on it —
         we must not silently pass a step the user did not perform. */
      if (lastGood) {
        return {
          frame: this.grabFrame(),
          descriptor: Array.from(lastGood.det.descriptor),
          pose: lastGood.pose,
          t: Date.now()
        };
      }
      throw new Error('No face was detected. Check your lighting and try again.');
    }

    /** Client-side hint only, to know when to grab the frame. Server decides. */
    _satisfies(action, pose, reference) {
      const ref = reference || { yaw: 0, pitch: 0 };
      const dy = pose.yaw - ref.yaw;
      const dp = pose.pitch - ref.pitch;

      switch (action) {
        case 'center':     return Math.abs(pose.yaw) < 22 && Math.abs(pose.pitch) < 22;
        case 'turn_left':
        case 'turn_right': return Math.abs(dy) > 9;
        case 'look_up':
        case 'look_down':  return Math.abs(dp) > 6;
        default:           return true;
      }
    }
  }

  global.NetraFaceLiveness = NetraFaceLiveness;
  global.NetraFaceLiveness.ensureModels = ensureModels;
})(window);
