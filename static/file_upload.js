/* ==========================================================================
   MenuFlash Mini — champ « Photo » compact (voir file_upload.css).
   - Bouton lisible « Photo » / « Fichier », puis nom du fichier + miniature.
   - Bouton × pour retirer le fichier choisi.
   - Contrôle immédiat du type et de la taille (5 Mo, comme le serveur) :
     évite d'envoyer un formulaire qui serait rejeté.
   Le serveur continue de tout vérifier (recompression de l'image, etc.).
   ========================================================================== */
(function () {
  "use strict";
  var MAX_BYTES = 5 * 1024 * 1024;
  var counter = 0;

  function enhance(input) {
    if (input.dataset.mfFile === "1" || input.closest(".mf-file")) return;
    input.dataset.mfFile = "1";
    if (!input.id) input.id = "mf-file-" + (++counter);

    var isImage = /image/.test(input.getAttribute("accept") || "") || input.name === "photo" || input.name === "logo";
    var emptyText = isImage ? "Photo" : "Choisir un fichier";
    var small = input.classList.contains("form-control-sm");

    var wrap = document.createElement("div");
    wrap.className = "mf-file" + (small ? " mf-file--sm" : "");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.classList.remove("form-control", "form-control-sm");

    var btn = document.createElement("label");
    btn.className = "mf-file-btn";
    btn.htmlFor = input.id;
    btn.title = "Choisir " + (isImage ? "une photo (JPG, PNG ou WEBP, 5 Mo max.)" : "un fichier");
    var icon = document.createElement("i");
    icon.className = "bi " + (isImage ? "bi-camera" : "bi-paperclip");
    var name = document.createElement("span");
    name.className = "mf-file-name";
    name.textContent = emptyText;
    btn.appendChild(icon); btn.appendChild(name);
    wrap.appendChild(btn);

    var thumb = document.createElement("img");
    thumb.className = "mf-file-thumb"; thumb.alt = ""; thumb.hidden = true;
    wrap.appendChild(thumb);

    var clear = document.createElement("button");
    clear.type = "button"; clear.className = "mf-file-clear"; clear.hidden = true;
    clear.setAttribute("aria-label", "Retirer le fichier");
    clear.innerHTML = '<i class="bi bi-x-lg"></i>';
    wrap.appendChild(clear);

    var error = document.createElement("div");
    error.className = "mf-file-error"; error.hidden = true; error.setAttribute("role", "alert");
    wrap.appendChild(error);

    var url = null;
    function reset(message) {
      input.value = "";
      if (url) { URL.revokeObjectURL(url); url = null; }
      thumb.hidden = true; thumb.removeAttribute("src");
      clear.hidden = true;
      wrap.classList.remove("has-file");
      name.textContent = emptyText;
      icon.className = "bi " + (isImage ? "bi-camera" : "bi-paperclip");
      error.textContent = message || ""; error.hidden = !message;
    }

    input.addEventListener("change", function () {
      var f = input.files && input.files[0];
      if (!f) { reset(); return; }
      if (f.size > MAX_BYTES) { reset("Fichier trop lourd (" + (f.size / 1048576).toFixed(1) + " Mo) : 5 Mo maximum."); return; }
      if (isImage && !/^image\/(jpeg|png|webp)$/i.test(f.type)) { reset("Format non accepté : JPG, PNG ou WEBP uniquement."); return; }
      error.hidden = true;
      wrap.classList.add("has-file");
      name.textContent = f.name;
      btn.title = f.name;
      icon.className = "bi bi-check-circle-fill";
      clear.hidden = false;
      if (isImage) {
        if (url) URL.revokeObjectURL(url);
        url = URL.createObjectURL(f);
        thumb.src = url; thumb.hidden = false;
      }
    });
    clear.addEventListener("click", function () { reset(); input.focus(); });
    var form = input.form;
    if (form) form.addEventListener("reset", function () { setTimeout(function () { reset(); }, 0); });
  }

  function run(root) {
    (root || document).querySelectorAll('input[type="file"]').forEach(enhance);
  }
  document.addEventListener("DOMContentLoaded", function () { run(document); });
})();
