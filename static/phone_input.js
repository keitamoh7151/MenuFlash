/* phone_input.js — aide à la saisie côté navigateur.
   La validation qui fait foi est côté serveur (phone_utils.py). */
(function () {
  // Longueur du numéro national SANS le 0 initial : [min, max]
  var LEN = {
    ML:[8,8], SN:[9,9], CI:[10,10], BF:[8,8], GN:[9,9], NE:[8,8], MR:[8,8],
    BJ:[8,10], TG:[8,8], CM:[9,9], GH:[9,9], NG:[8,10], MA:[9,9], DZ:[8,9],
    TN:[8,8], FR:[9,9], BE:[8,9], CH:[9,9], CA:[10,10], US:[10,10]
  };
  var KEEP_ZERO = { CI:1, BJ:1 }; // le 0 fait partie du numéro

  function digits(v, code) {
    var d = (v || "").replace(/[^\d]/g, "");
    if (!KEEP_ZERO[code] && d.length > 1 && d.charAt(0) === "0") d = d.slice(1);
    return d;
  }

  function check(field) {
    var sel = field.querySelector("[data-phone-country]");
    var input = field.querySelector("[data-phone-input]");
    var msg = field.querySelector("[data-phone-msg]");
    var opt = sel.options[sel.selectedIndex];
    var code = sel.value, example = opt.getAttribute("data-example");
    input.placeholder = "Ex : " + example;

    var raw = input.value.trim();
    if (!raw) { input.setCustomValidity(""); field.classList.remove("is-invalid","is-valid"); msg.textContent = ""; return; }

    if (raw.charAt(0) === "+" || raw.indexOf("00") === 0) {
      var dial = opt.getAttribute("data-dial").replace("+", "");
      var intl = raw.replace(/[^\d]/g, "").replace(/^00/, "");
      if (intl.indexOf(dial) !== 0) {
        return fail("L'indicatif ne correspond pas au pays choisi (" + opt.getAttribute("data-dial") + ").");
      }
      raw = intl.slice(dial.length);
    }
    var d = digits(raw, code), r = LEN[code] || [6, 15];
    if (d.length < r[0] || d.length > r[1]) {
      return fail("Format invalide pour " + opt.text.split(" (")[0] + ". Exemple : " + example);
    }
    input.setCustomValidity("");
    field.classList.remove("is-invalid"); field.classList.add("is-valid");
    msg.textContent = "✓ " + opt.getAttribute("data-dial") + " " + d;

    function fail(t) {
      input.setCustomValidity(t);
      field.classList.remove("is-valid"); field.classList.add("is-invalid");
      msg.textContent = t;
    }
  }

  function init(field) {
    if (field.dataset.phoneInit) return;
    field.dataset.phoneInit = "1";
    var sel = field.querySelector("[data-phone-country]");
    var input = field.querySelector("[data-phone-input]");
    sel.addEventListener("change", function () { check(field); });
    input.addEventListener("input", function () {
      input.value = input.value.replace(/[^\d+\s().-]/g, "");
      check(field);
    });
    check(field);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-phone-field]").forEach(init);
  });
})();
