/**
 * Living Parchment Sensory Module · Influx Vision
 * Refined tactile, acoustic, optical, and circadian depth without excess.
 */

(function() {
  // 1. Acoustic Whisper (Synthesized Web Audio)
  let audioCtx = null;
  function initAudio() {
    if (!audioCtx) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) audioCtx = new AudioContext();
    }
  }

  // Soft ceramic clink (880Hz / 1760Hz damped sine wave)
  window.playCeramicWhisper = function() {
    try {
      initAudio();
      if (!audioCtx) return;
      if (audioCtx.state === 'suspended') audioCtx.resume();
      
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(1760, now + 0.02);
      osc.frequency.exponentialRampToValueAtTime(440, now + 0.08);

      gain.gain.setValueAtTime(0.018, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);

      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.12);
    } catch (_) {}
  };

  // Subtle horological escapement tick (1200Hz impulse)
  window.playEscapementTick = function() {
    try {
      initAudio();
      if (!audioCtx) return;
      if (audioCtx.state === 'suspended') audioCtx.resume();
      
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(1400, now);
      osc.frequency.exponentialRampToValueAtTime(300, now + 0.015);

      gain.gain.setValueAtTime(0.012, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.025);

      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.025);
    } catch (_) {}
  };

  // 2. Interactive Paper Sheen (Pointer-coupled diffuse highlight)
  function initPaperSheen() {
    document.addEventListener('mousemove', e => {
      const cards = document.querySelectorAll('.card, .tea-card, .urushi-card, .monolith-card, .feature, .panel, .sentinel-card');
      cards.forEach(card => {
        const rect = card.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        if (x >= -40 && x <= rect.width + 40 && y >= -40 && y <= rect.height + 40) {
          card.style.setProperty('--mouse-x', `${x}px`);
          card.style.setProperty('--mouse-y', `${y}px`);
        }
      });
    }, { passive: true });
  }

  // 3. Circadian Daylight Calibration
  function applyCircadianCalibration() {
    if (document.body.classList.contains('mode-nocturnal')) return;
    const hour = new Date().getHours();
    
    if (hour >= 5 && hour < 9) {
      document.documentElement.style.setProperty('--paper-soft', '#fcf8f6');
    } else if (hour >= 9 && hour < 17) {
      document.documentElement.style.setProperty('--paper-soft', '#fcfbf8');
    } else if (hour >= 17 && hour < 21) {
      document.documentElement.style.setProperty('--paper-soft', '#f8f4ec');
    }
  }

  document.addEventListener('click', e => {
    if (e.target.closest('.copy-btn') || e.target.closest('.ritual-switch') || e.target.closest('.dock-btn')) {
      window.playCeramicWhisper();
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initPaperSheen();
      applyCircadianCalibration();
    });
  } else {
    initPaperSheen();
    applyCircadianCalibration();
  }
})();
