/*
 * MenuFlash Mini — sons de notification de l'équipe (cuisine, salle, gérant).
 *
 *   MFSound.play("new_order")  nouvelle commande
 *   MFSound.play("reminder")   rappel : commande pas encore prise en charge
 *   MFSound.play("updated")    plats ajoutés à une commande
 *   MFSound.play("cancelled")  commande annulée
 *   MFSound.play("call")       appel d'une table (serveur / addition)
 *
 * STYLES DE SON (choisis sur chaque appareil, dans ⚙ de la page Commandes)
 * ----------------------------------------------------------------------
 *   marimba    « Livraison » : marimba vif et joyeux, style applis de livraison (par défaut)
 *   carillon   carillon de comptoir « ding-dong »
 *   xylophone  xylophone rapide et brillant
 *   bulle      « pop » rond et moderne, style messagerie
 *   harpe      arpège doux de harpe / kalimba
 *   fichiers   VOS propres sons MP3 (voir ci-dessous)
 *
 * Tous les styles synthétisés sont créés par le navigateur : aucun fichier à
 * télécharger, rien à payer, aucun problème de droits d'auteur.
 *
 * SONS PERSONNALISÉS (style « fichiers »)
 * ---------------------------------------
 * Déposez des fichiers MP3 libres de droits (ex. Pixabay, « New Notification »)
 * dans static/sounds/ avec ces noms exacts :
 *     static/sounds/new_order.mp3   (obligatoire pour ce style)
 *     static/sounds/updated.mp3     static/sounds/cancelled.mp3
 *     static/sounds/call.mp3        static/sounds/reminder.mp3
 * Un fichier absent est remplacé automatiquement par le son « marimba ».
 *
 * Volume : Normal / Fort / Très fort, avec un compresseur pour un son plus
 * présent sans saturer. Le maximum reste le volume de l'appareil.
 */
(function () {
  "use strict";
  if (window.MFSound) return;

  var VOLUME_KEY = "mf_sound_volume";
  var STYLE_KEY = "mf_sound_style";
  var LEVELS = { normal: 0.55, fort: 0.85, tres_fort: 1.0 };
  var STYLES = ["marimba", "carillon", "xylophone", "bulle", "harpe", "fichiers"];
  var KINDS = ["new_order", "reminder", "updated", "cancelled", "call"];
  var FILE_BASE = "/static/sounds/";
  var DEDUP_MS = 2500;   // même son reçu 2 fois (temps réel + sondage) : joué une seule fois

  var ctx = null, master = null, lastPlayed = {}, buffers = {}, loading = {};

  function read(key, allowed, fallback) {
    try {
      var v = localStorage.getItem(key);
      return allowed.indexOf(v) !== -1 ? v : fallback;
    } catch (e) { return fallback; }
  }
  function level() { return read(VOLUME_KEY, Object.keys(LEVELS), "fort"); }
  function style() { return read(STYLE_KEY, STYLES, "marimba"); }

  function setLevel(v) {
    if (!LEVELS[v]) return;
    try { localStorage.setItem(VOLUME_KEY, v); } catch (e) {}
    if (master) master.gain.value = LEVELS[v];
  }
  function setStyle(v) {
    if (STYLES.indexOf(v) === -1) return;
    try { localStorage.setItem(STYLE_KEY, v); } catch (e) {}
    if (v === "fichiers") preloadFiles();
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

  // Le navigateur bloque le son tant que la page n'a pas été touchée.
  function unlock() {
    audio();
    if (style() === "fichiers") preloadFiles();
  }
  ["pointerdown", "keydown", "touchstart"].forEach(function (ev) {
    document.addEventListener(ev, unlock, { passive: true, capture: true });
  });

  /* ------------------------------ Instruments ------------------------------ */
  // Chaque instrument = liste de [rapport de fréquence, intensité, durée relative, forme d'onde].
  var INSTRUMENTS = {
    // Marimba : attaque franche, son rond et boisé (harmonique 4 caractéristique).
    marimba:   { partials: [[1, 1.0, 1.0, "sine"], [4, 0.35, 0.25, "sine"], [10, 0.08, 0.08, "sine"]], attack: 0.004 },
    // Cloche de comptoir.
    carillon:  { partials: [[1, 1.0, 1.0, "triangle"], [2, 0.42, 0.62, "sine"], [3, 0.2, 0.45, "sine"], [4.2, 0.1, 0.35, "sine"]], attack: 0.012 },
    // Xylophone : plus sec et plus brillant que le marimba.
    xylophone: { partials: [[1, 1.0, 1.0, "sine"], [3, 0.45, 0.35, "sine"], [6.3, 0.18, 0.15, "sine"]], attack: 0.002 },
    // Bulle : sinus dont la hauteur glisse vers le haut (« pop »).
    bulle:     { partials: [[1, 1.0, 1.0, "sine"], [2, 0.15, 0.5, "sine"]], attack: 0.003, glide: 1.5 },
    // Harpe / kalimba : douce, chaude.
    harpe:     { partials: [[1, 1.0, 1.0, "triangle"], [2, 0.3, 0.7, "sine"], [3, 0.12, 0.5, "sine"]], attack: 0.006 }
  };

  function note(c, inst, freq, start, length, peak) {
    inst.partials.forEach(function (p) {
      var o = c.createOscillator(), g = c.createGain();
      o.type = p[3];
      var f = freq * p[0];
      if (inst.glide) {
        o.frequency.setValueAtTime(f, start);
        o.frequency.exponentialRampToValueAtTime(f * inst.glide, start + 0.09);
      } else {
        o.frequency.value = f;
      }
      var dur = Math.max(0.06, length * p[2]);
      g.gain.setValueAtTime(0.0001, start);
      g.gain.exponentialRampToValueAtTime(peak * p[1], start + inst.attack);
      g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
      o.connect(g); g.connect(master);
      o.start(start); o.stop(start + dur + 0.05);
    });
  }

  /* ------------------------------- Mélodies ------------------------------- */
  // Notes (Hz)
  var N = { G4: 392, A4: 440, C5: 523.25, D5: 587.33, E5: 659.25, G5: 783.99, A5: 880,
            C6: 1046.5, D6: 1174.7, E6: 1318.5, G6: 1568, C7: 2093 };
  // [note, départ (s), durée (s), intensité]
  var MELODIES = {
    marimba: {
      // Motif montant joyeux, joué deux fois : « ta-da-ding … ta-da-ding ».
      new_order: [[N.C6, 0, .5, .9], [N.E6, .12, .5, .9], [N.G6, .24, .9, 1],
                  [N.C6, .9, .5, .9], [N.E6, 1.02, .5, .9], [N.G6, 1.14, 1.1, 1]],
      reminder:  [[N.C6, 0, .5, .85], [N.E6, .12, .5, .85], [N.G6, .24, .9, .9]],
      updated:   [[N.G5, 0, .5, .8], [N.C6, .14, .8, .85]],
      cancelled: [[N.G5, 0, .5, .8], [N.E5, .16, .5, .8], [N.C5, .32, .9, .8]],
      call:      [[N.E6, 0, .4, .8], [N.E6, .16, .4, .8], [N.C7, .32, .8, .85]]
    },
    carillon: {
      new_order: [[N.E5, 0, 1.1, .9], [N.C5, .42, 1.4, .9], [N.E5, 1.25, 1.1, .9], [N.C5, 1.67, 1.6, .9]],
      reminder:  [[N.E5, 0, 1.0, .8], [N.C5, .42, 1.4, .8]],
      updated:   [[N.G5, 0, .7, .75], [N.C6, .2, 1.0, .75]],
      cancelled: [[N.G5, 0, .6, .7], [N.E5, .22, .6, .7], [N.C5, .44, 1.1, .7]],
      call:      [[N.C6, 0, .6, .65], [N.E6, .14, .6, .65], [N.G6, .28, .9, .65],
                  [N.C6, .75, .6, .65], [N.E6, .89, .6, .65], [N.G6, 1.03, 1.0, .65]]
    },
    xylophone: {
      new_order: [[N.G5, 0, .35, .85], [N.C6, .09, .35, .85], [N.E6, .18, .35, .9], [N.G6, .27, .7, 1],
                  [N.G5, .85, .35, .85], [N.C6, .94, .35, .85], [N.E6, 1.03, .35, .9], [N.G6, 1.12, .8, 1]],
      reminder:  [[N.G5, 0, .35, .8], [N.C6, .09, .35, .8], [N.E6, .18, .35, .85], [N.G6, .27, .7, .9]],
      updated:   [[N.E6, 0, .35, .8], [N.G6, .1, .6, .85]],
      cancelled: [[N.G6, 0, .3, .75], [N.E6, .1, .3, .75], [N.C6, .2, .3, .75], [N.G5, .3, .6, .75]],
      call:      [[N.C7, 0, .3, .7], [N.G6, .1, .3, .7], [N.C7, .2, .6, .75]]
    },
    bulle: {
      new_order: [[N.C6, 0, .25, .9], [N.G6, .14, .3, 1], [N.C6, .7, .25, .9], [N.G6, .84, .35, 1]],
      reminder:  [[N.C6, 0, .25, .85], [N.G6, .14, .3, .9]],
      updated:   [[N.G6, 0, .3, .85]],
      cancelled: [[N.G5, 0, .25, .8], [N.C5, .16, .35, .8]],
      call:      [[N.E6, 0, .2, .8], [N.E6, .14, .2, .8], [N.E6, .28, .3, .85]]
    },
    harpe: {
      new_order: [[N.C5, 0, 1.2, .8], [N.E5, .08, 1.2, .8], [N.G5, .16, 1.2, .85], [N.C6, .24, 1.2, .9],
                  [N.E6, .32, 1.6, .95]],
      reminder:  [[N.G5, 0, 1.0, .8], [N.C6, .08, 1.0, .85], [N.E6, .16, 1.4, .9]],
      updated:   [[N.C6, 0, .9, .8], [N.E6, .1, 1.2, .85]],
      cancelled: [[N.E6, 0, .8, .75], [N.C6, .12, .8, .75], [N.A5, .24, .8, .75], [N.E5, .36, 1.2, .75]],
      call:      [[N.G5, 0, .7, .75], [N.D6, .1, .7, .75], [N.G6, .2, 1.1, .8]]
    }
  };

  function playSynth(kind, styleName) {
    var c = audio();
    if (!c) return;
    var inst = INSTRUMENTS[styleName] || INSTRUMENTS.marimba;
    var melody = (MELODIES[styleName] || MELODIES.marimba)[kind] || MELODIES.marimba.new_order;
    var t0 = c.currentTime + 0.03;
    melody.forEach(function (n) { note(c, inst, n[0], t0 + n[1], n[2], n[3]); });
  }

  /* --------------------------- Fichiers MP3 perso --------------------------- */
  function loadFile(kind) {
    if (buffers[kind] !== undefined || loading[kind]) return loading[kind] || Promise.resolve(buffers[kind]);
    var c = audio();
    if (!c) return Promise.resolve(null);
    loading[kind] = fetch(FILE_BASE + kind + ".mp3", { cache: "force-cache" })
      .then(function (r) { return r.ok ? r.arrayBuffer() : null; })
      .then(function (data) {
        if (!data) return null;
        return new Promise(function (resolve) {
          c.decodeAudioData(data, resolve, function () { resolve(null); });
        });
      })
      .catch(function () { return null; })
      .then(function (buf) { buffers[kind] = buf || null; delete loading[kind]; return buffers[kind]; });
    return loading[kind];
  }
  function preloadFiles() {
    if (!ctx) return;                    // attend le premier geste de l'utilisateur
    KINDS.forEach(loadFile);
  }
  function playFile(kind) {
    var fileKind = kind === "reminder" ? "reminder" : kind;
    loadFile(fileKind).then(function (buf) {
      if (!buf && fileKind === "reminder") return loadFile("new_order");
      return buf;
    }).then(function (buf) {
      if (!buf) { playSynth(kind, "marimba"); return; }
      var c = audio();
      var src = c.createBufferSource();
      src.buffer = buf;
      src.connect(master);
      src.start(c.currentTime + 0.02);
    });
  }

  /* --------------------------------- Lecture --------------------------------- */
  function play(kind, forceStyle) {
    if (KINDS.indexOf(kind) === -1) kind = "new_order";
    var now = Date.now();
    if (!forceStyle) {
      if (lastPlayed[kind] && now - lastPlayed[kind] < DEDUP_MS) return;
      lastPlayed[kind] = now;
    }
    var s = forceStyle || style();
    try {
      if (s === "fichiers") playFile(kind);
      else playSynth(kind, s);
    } catch (e) { /* audio indisponible sur cet appareil */ }
    if (kind === "new_order" && navigator.vibrate) {
      try { navigator.vibrate([200, 100, 200]); } catch (e) {}
    }
  }

  window.MFSound = {
    play: play,
    level: level,
    setLevel: setLevel,
    style: style,
    setStyle: setStyle,
    styles: STYLES,
    unlock: unlock
  };
})();
