/*
 * MenuFlash Mini — sons de notification de l'équipe (cuisine, salle, gérant).
 *
 * Objectif : des sons FORTS, AGRÉABLES et faciles à reconnaître de loin,
 * même dans une cuisine bruyante, sans aucun fichier audio à télécharger.
 *
 *   MFSound.play("new_order")  nouvelle commande : carillon « ding-dong » joué 2 fois
 *   MFSound.play("reminder")   rappel : commande pas encore prise en charge
 *   MFSound.play("updated")    plats ajoutés à une commande : double « ding » clair
 *   MFSound.play("cancelled")  commande annulée : trois notes descendantes
 *   MFSound.play("call")       appel d'une table (serveur / addition) : arpège montant
 *
 * Comment c'est fort sans être agressif :
 * - timbre de cloche (fondamentale + harmoniques douces) dans les fréquences
 *   que l'oreille perçoit le mieux (500 à 2 500 Hz), qui passent au-dessus du
 *   bruit d'une hotte ou d'une friteuse ;
 * - un compresseur égalise le son : il paraît beaucoup plus fort sans saturer ;
 * - pas de bip carré strident.
 *
 * Réglage du volume (Normal / Fort / Très fort) mémorisé SUR CET APPAREIL.
 * Le volume maximum reste celui de l'appareil : pensez à monter le volume de
 * la tablette ou du PC, ou à brancher une enceinte Bluetooth.
 */
(function () {
  "use strict";
  if (window.MFSound) return;

  var VOLUME_KEY = "mf_sound_volume";
  var LEVELS = { normal: 0.55, fort: 0.85, tres_fort: 1.0 };
  var DEDUP_MS = 2500;                // même son reçu 2 fois (temps réel + sondage) : joué une fois

  var ctx = null, master = null, lastPlayed = {};

  function level() {
    try {
      var v = localStorage.getItem(VOLUME_KEY);
      return LEVELS[v] ? v : "fort";
    } catch (e) { return "fort"; }
  }

  function setLevel(v) {
    if (!LEVELS[v]) return;
    try { localStorage.setItem(VOLUME_KEY, v); } catch (e) {}
    if (master) master.gain.value = LEVELS[v];
  }

  function audio() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!ctx) {
      ctx = new Ctx();
      var comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -24;
      comp.knee.value = 12;
      comp.ratio.value = 6;
      comp.attack.value = 0.003;
      comp.release.value = 0.25;
      master = ctx.createGain();
      master.gain.value = LEVELS[level()];
      master.connect(comp);
      comp.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume().catch(function () {});
    return ctx;
  }

  // Les navigateurs bloquent le son tant que l'utilisateur n'a pas touché la
  // page : on prépare le moteur audio dès le premier geste.
  function unlock() { audio(); }
  ["pointerdown", "keydown", "touchstart"].forEach(function (ev) {
    document.addEventListener(ev, unlock, { passive: true, capture: true });
  });

  // Une note « cloche » : fondamentale + harmoniques qui s'éteignent doucement.
  var PARTIALS = [[1, 1.0], [2, 0.42], [3, 0.2], [4.2, 0.1]];
  function bell(c, freq, start, length, peak) {
    PARTIALS.forEach(function (p, i) {
      var o = c.createOscillator(), g = c.createGain();
      o.type = i === 0 ? "triangle" : "sine";
      o.frequency.value = freq * p[0];
      var amp = peak * p[1];
      var dur = length / (1 + i * 0.6);   // les aigus s'éteignent plus vite, comme une vraie cloche
      g.gain.setValueAtTime(0.0001, start);
      g.gain.exponentialRampToValueAtTime(amp, start + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
      o.connect(g); g.connect(master);
      o.start(start); o.stop(start + dur + 0.05);
    });
  }

  // [fréquence (Hz), départ (s), durée (s), intensité]
  var E5 = 659.25, C5 = 523.25, G5 = 783.99, C6 = 1046.5, E6 = 1318.5, G6 = 1568;
  var PATTERNS = {
    new_order: [[E5, 0, 1.1, 0.9], [C5, 0.42, 1.4, 0.9], [E5, 1.25, 1.1, 0.9], [C5, 1.67, 1.6, 0.9]],
    reminder:  [[E5, 0, 1.0, 0.8], [C5, 0.42, 1.4, 0.8]],
    updated:   [[G5, 0, 0.7, 0.75], [C6, 0.2, 1.0, 0.75]],
    cancelled: [[G5, 0, 0.6, 0.7], [E5, 0.22, 0.6, 0.7], [C5, 0.44, 1.1, 0.7]],
    call:      [[C6, 0, 0.6, 0.65], [E6, 0.14, 0.6, 0.65], [G6, 0.28, 0.9, 0.65],
                [C6, 0.75, 0.6, 0.65], [E6, 0.89, 0.6, 0.65], [G6, 1.03, 1.0, 0.65]]
  };

  function play(kind) {
    var pattern = PATTERNS[kind] || PATTERNS.new_order;
    var now = Date.now();
    if (lastPlayed[kind] && now - lastPlayed[kind] < DEDUP_MS) return;
    lastPlayed[kind] = now;
    try {
      var c = audio();
      if (!c) return;
      var t0 = c.currentTime + 0.03;
      pattern.forEach(function (n) { bell(c, n[0], t0 + n[1], n[2], n[3]); });
    } catch (e) { /* audio indisponible sur cet appareil */ }
    // Vibration en plus sur les téléphones Android (ignorée ailleurs).
    if (kind === "new_order" && navigator.vibrate) {
      try { navigator.vibrate([200, 100, 200]); } catch (e) {}
    }
  }

  window.MFSound = {
    play: play,
    level: level,
    setLevel: setLevel,
    levels: ["normal", "fort", "tres_fort"],
    unlock: unlock
  };
})();
