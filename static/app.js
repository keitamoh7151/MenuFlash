/* ==========================================================================
   MenuFlash Mini — interactivité JS (progressive enhancement)
   ========================================================================== */
(function () {
  "use strict";

  (function installCsrfFetchGuard() {
    var nativeFetch = window.fetch;
    if (!nativeFetch || nativeFetch.__mfCsrfWrapped) return;

    function getCsrfToken() {
      var meta = document.querySelector('meta[name="csrf-token"]');
      return meta ? meta.getAttribute("content") : "";
    }

    function wrappedFetch(input, init) {
      init = init || {};
      var method = (init.method || "GET").toUpperCase();
      if (method !== "GET" && method !== "HEAD") {
        var token = getCsrfToken();
        if (init.headers instanceof Headers) {
          if (!init.headers.has("X-CSRFToken")) init.headers.set("X-CSRFToken", token);
        } else {
          init.headers = init.headers || {};
          if (!("X-CSRFToken" in init.headers)) init.headers["X-CSRFToken"] = token;
        }
      }
      return nativeFetch(input, init);
    }
    wrappedFetch.__mfCsrfWrapped = true;
    window.fetch = wrappedFetch;
  })();

  function formatNumber(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function showToast(message, isError) {
    var container = document.getElementById("toast-container");
    if (!container || typeof bootstrap === "undefined") return;
    var icon = isError ? "bi-exclamation-circle-fill text-danger" : "bi-check-circle-fill text-success";
    var toastEl = document.createElement("div");
    toastEl.className = "toast align-items-center border-0 shadow mb-2";
    toastEl.setAttribute("role", "alert");
    toastEl.innerHTML =
      '<div class="d-flex">' +
        '<div class="toast-body d-flex align-items-center gap-2">' +
          '<i class="bi ' + icon + '"></i><span>' + message + '</span>' +
        '</div>' +
        '<button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast"></button>' +
      '</div>';
    container.appendChild(toastEl);
    var toast = new bootstrap.Toast(toastEl, { delay: 6000 });
    toast.show();
  }
  window.MFShowToast = showToast;

  function playNotificationSound() {
    try {
      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      var ctx = new AudioCtx();
      var notes = [880, 1046.5];
      notes.forEach(function (freq, i) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        var startTime = ctx.currentTime + i * 0.16;
        gain.gain.setValueAtTime(0.0001, startTime);
        gain.gain.exponentialRampToValueAtTime(0.35, startTime + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.32);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(startTime);
        osc.stop(startTime + 0.35);
      });
      setTimeout(function () { ctx.close(); }, 900);
    } catch (e) { /* audio non disponible, tant pis */ }
  }

  function applyThemeVars(colors) {
    if (!colors) return;
    var root = document.documentElement;
    if (colors.accent) root.style.setProperty("--mf-accent", colors.accent);
    if (colors.accent_dark) root.style.setProperty("--mf-accent-dark", colors.accent_dark);
    if (colors.accent_light) root.style.setProperty("--mf-accent-light", colors.accent_light);

    if (window.MFPeakHoursChart && colors.accent) {
      window.MFPeakHoursChart.data.datasets[0].backgroundColor = colors.accent;
      window.MFPeakHoursChart.update();
    }
  }
  window.MFApplyTheme = applyThemeVars;

  var MF_LANG_KEY = "mf_lang";

  function loadJsonScript(elementId) {
    var el = document.getElementById(elementId);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function getCurrentLang() {
    try { return localStorage.getItem(MF_LANG_KEY) || "fr"; } catch (e) { return "fr"; }
  }

  function translateKey(key) {
    var dict = window.MF_I18N_DICT;
    var lang = getCurrentLang();
    if (!dict || !dict[lang] || !dict[lang][key]) return null;
    return dict[lang][key];
  }
  window.MFTranslate = translateKey;

  function applyLangToDom(lang) {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      var dict = window.MF_I18N_DICT;
      if (dict && dict[lang] && dict[lang][key] != null) {
        el.textContent = dict[lang][key];
      }
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-placeholder");
      var dict = window.MF_I18N_DICT;
      if (dict && dict[lang] && dict[lang][key] != null) {
        el.setAttribute("placeholder", dict[lang][key]);
      }
    });
    document.querySelectorAll(".mf-lang-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.lang === lang);
    });
  }

  function setLang(lang) {
    try { localStorage.setItem(MF_LANG_KEY, lang); } catch (e) {}
    applyLangToDom(lang);
  }
  window.MFSetLang = setLang;

  function initI18n() {
    var dict = loadJsonScript("mf-i18n-data");
    if (!dict) return;
    window.MF_I18N_DICT = dict;

    document.querySelectorAll(".mf-lang-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setLang(btn.dataset.lang);
      });
    });

    applyLangToDom(getCurrentLang());
  }

  function initToasts() {
    var dataEl = document.getElementById("flash-data");
    if (!dataEl) return;
    var messages;
    try { messages = JSON.parse(dataEl.textContent); } catch (e) { return; }
    if (!Array.isArray(messages)) return;
    messages.forEach(function (pair, index) {
      setTimeout(function () { showToast(pair[1], pair[0] === "error"); }, index * 150);
    });
  }

  function initConfirmModal() {
    var modalEl = document.getElementById("mf-confirm-modal");
    if (!modalEl || typeof bootstrap === "undefined") return;
    var modal = new bootstrap.Modal(modalEl);
    var messageEl = document.getElementById("mf-confirm-message");
    var confirmBtn = document.getElementById("mf-confirm-btn");
    var pendingForm = null;

    document.querySelectorAll("form.mf-confirm").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        pendingForm = form;
        messageEl.textContent = form.dataset.confirmMessage || "Confirmer cette action ?";
        modal.show();
      });
    });

    confirmBtn.addEventListener("click", function () {
      modal.hide();
      if (pendingForm) {
        pendingForm.submit();
        pendingForm = null;
      }
    });
  }

  function initCancelOrderModal() {
    var modalEl = document.getElementById("cancel-order-modal");
    if (!modalEl || typeof bootstrap === "undefined") return null;
    var modal = new bootstrap.Modal(modalEl);
    var confirmBtn = document.getElementById("cancel-order-confirm-btn");
    var pendingCallback = null;

    confirmBtn.addEventListener("click", function () {
      var labelEl = confirmBtn.querySelector(".confirm-label");
      var spinnerEl = confirmBtn.querySelector(".spinner-border");
      if (spinnerEl) spinnerEl.classList.remove("d-none");
      confirmBtn.disabled = true;

      var cb = pendingCallback;
      pendingCallback = null;

      var finish = function () {
        confirmBtn.disabled = false;
        if (spinnerEl) spinnerEl.classList.add("d-none");
        modal.hide();
      };

      if (cb) {
        cb(finish);
      } else {
        finish();
      }
    });

    return function requestCancelConfirmation(onConfirmed) {
      pendingCallback = onConfirmed;
      modal.show();
    };
  }

  /* ------------------------------------------------------------------
     SÉLECTEUR DE RESTAURANT — ouverture de la modale "Nouveau restaurant"
     depuis l'item "Nouveau restaurant" du dropdown. Ferme explicitement
     le dropdown AVANT d'ouvrir la modale (corrige un bug Bootstrap connu
     où les deux widgets se disputent focus/backdrop si ouverts en même
     temps depuis un data-bs-toggle imbriqué).
     ------------------------------------------------------------------ */
  function initRestaurantSwitcherModal() {
    var openBtn = document.getElementById("open-new-restaurant-modal-btn");
    var modalEl = document.getElementById("new-restaurant-modal");
    if (!openBtn || !modalEl || typeof bootstrap === "undefined") return;

    openBtn.addEventListener("click", function () {
      var toggleBtn = document.getElementById("mf-restaurant-switcher-toggle");
      var dropdownInstance = toggleBtn ? bootstrap.Dropdown.getInstance(toggleBtn) : null;
      var modalShown = false;

      function showModalOnce() {
        if (modalShown) return;
        modalShown = true;
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
      }

      if (dropdownInstance && toggleBtn.classList.contains("show")) {
        toggleBtn.addEventListener("hidden.bs.dropdown", showModalOnce, { once: true });
        dropdownInstance.hide();
        setTimeout(showModalOnce, 300);
      } else {
        showModalOnce();
      }
    });
  }

  function initOrderCartAndMyOrders(requestCancelConfirmation) {
    var form = document.getElementById("order-form");
    if (!form) return;

    var slug = form.dataset.slug;
    var currency = form.dataset.currency || "";
    var storageKey = "mf_orders_" + slug;

    var cartBar = document.getElementById("cart-bar");
    var countEl = document.getElementById("cart-count");
    var totalEl = document.getElementById("cart-total");
    var submitBtn = document.getElementById("cart-submit");
    var submitLabelEl = document.getElementById("cart-submit-label");

    var myOrdersList = document.getElementById("my-orders-list");
    var myOrdersEmpty = document.getElementById("my-orders-empty");
    var myOrdersBadge = document.getElementById("my-orders-badge");

    var addmodeBanner = document.getElementById("addmode-banner");
    var addmodeOrderRef = document.getElementById("addmode-order-ref");
    var addmodeCancelBtn = document.getElementById("addmode-cancel");

    var addModeOrderId = null;

    var suggestionsDict = loadJsonScript("mf-suggestions-data") || {};
    var nudgedItemIds = new Set();

    function getSuggestionsFor(itemId) {
      return suggestionsDict[String(itemId)] || [];
    }

    function computeUpsellSuggestions(cartItemIds) {
      var inCart = new Set(cartItemIds);
      var seen = new Set();
      var result = [];
      cartItemIds.forEach(function (id) {
        getSuggestionsFor(id).forEach(function (sug) {
          if (!inCart.has(sug.id) && !seen.has(sug.id)) {
            seen.add(sug.id);
            result.push(sug);
          }
        });
      });
      return result.slice(0, 4);
    }

    function getStoredIds() {
      try {
        var raw = localStorage.getItem(storageKey);
        var ids = raw ? JSON.parse(raw) : [];
        return Array.isArray(ids) ? ids : [];
      } catch (e) { return []; }
    }
    function addStoredId(id) {
      var ids = getStoredIds();
      if (ids.indexOf(id) === -1) {
        ids.push(id);
        try { localStorage.setItem(storageKey, JSON.stringify(ids)); } catch (e) {}
      }
    }

    function cartArticleLabel(count) {
      var key = count > 1 ? "cart_article_other" : "cart_article_one";
      return translateKey(key) || (count > 1 ? "articles" : "article");
    }

    function readCart() {
      var count = 0, total = 0, lines = [], ids = [];
      form.querySelectorAll(".mf-qty-group").forEach(function (group) {
        var qty = parseInt(group.querySelector(".mf-qty-value").textContent, 10) || 0;
        var price = parseInt(group.dataset.price, 10) || 0;
        var name = group.dataset.name || "";
        var itemId = parseInt(group.dataset.itemId, 10);
        if (qty > 0) {
          count += qty;
          total += qty * price;
          lines.push({ name: name, qty: qty, price: price });
          if (!isNaN(itemId)) ids.push(itemId);
        }
      });
      return { count: count, total: total, lines: lines, ids: ids };
    }

    function updateCartUI() {
      var cart = readCart();
      if (countEl) countEl.textContent = cart.count + " " + cartArticleLabel(cart.count);
      if (totalEl) totalEl.textContent = formatNumber(cart.total) + " " + currency;
      if (submitBtn) submitBtn.disabled = cart.count === 0;
      if (cartBar) cartBar.classList.toggle("is-visible", cart.count > 0);
      return cart;
    }

    function resetCart() {
      form.querySelectorAll(".mf-qty-group").forEach(function (group) {
        group.querySelector(".mf-qty-value").textContent = "0";
        group.querySelector("input[type=hidden]").value = "0";
      });
      updateCartUI();
    }

    function incrementItemQty(itemId) {
      var group = form.querySelector('.mf-qty-group[data-item-id="' + itemId + '"]');
      if (!group) return;
      var valueEl = group.querySelector(".mf-qty-value");
      var hiddenInput = group.querySelector("input[type=hidden]");
      var newQty = Math.min((parseInt(hiddenInput.value, 10) || 0) + 1, 50);
      hiddenInput.value = newQty;
      valueEl.textContent = newQty;
      valueEl.classList.remove("mf-pop");
      void valueEl.offsetWidth;
      valueEl.classList.add("mf-pop");
    }

    form.querySelectorAll(".mf-qty-group").forEach(function (group) {
      var valueEl = group.querySelector(".mf-qty-value");
      var hiddenInput = group.querySelector("input[type=hidden]");
      var minusBtn = group.querySelector(".mf-qty-minus");
      var plusBtn = group.querySelector(".mf-qty-plus");
      var itemId = parseInt(group.dataset.itemId, 10);

      function setQty(q) {
        var previous = parseInt(hiddenInput.value, 10) || 0;
        q = Math.max(0, Math.min(50, q));
        valueEl.textContent = q;
        hiddenInput.value = q;
        valueEl.classList.remove("mf-pop");
        void valueEl.offsetWidth;
        valueEl.classList.add("mf-pop");
        updateCartUI();

        if (previous === 0 && q > 0 && !isNaN(itemId) && !nudgedItemIds.has(itemId)) {
          var cart = readCart();
          var sugg = computeUpsellSuggestions(cart.ids).filter(function (s) {
            return getSuggestionsFor(itemId).some(function (own) { return own.id === s.id; });
          });
          if (sugg.length > 0) {
            nudgedItemIds.add(itemId);
            var prefix = translateKey("upsell_nudge_prefix") || "💡 Nos clients ajoutent souvent :";
            var names = sugg.map(function (s) { return s.name; }).join(", ");
            showToast(prefix + " " + names, false);
          }
        }
      }

      minusBtn.addEventListener("click", function () {
        setQty((parseInt(hiddenInput.value, 10) || 0) - 1);
      });
      plusBtn.addEventListener("click", function () {
        setQty((parseInt(hiddenInput.value, 10) || 0) + 1);
      });
    });

    function enterAddMode(orderId, tableLabel) {
      addModeOrderId = orderId;
      resetCart();
      if (addmodeBanner) {
        addmodeBanner.classList.remove("d-none");
        if (addmodeOrderRef) {
          addmodeOrderRef.textContent = "#" + orderId + (tableLabel ? " · " + tableLabel : "");
        }
      }
      if (submitLabelEl) submitLabelEl.textContent = (translateKey("add_dishes_btn") || "Ajouter à la commande") + " #" + orderId;
      if (cartBar) cartBar.classList.add("mf-cart-addmode");

      var offcanvasEl = document.getElementById("my-orders-offcanvas");
      if (offcanvasEl && typeof bootstrap !== "undefined") {
        var instance = bootstrap.Offcanvas.getInstance(offcanvasEl);
        if (instance) instance.hide();
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function exitAddMode() {
      addModeOrderId = null;
      if (addmodeBanner) addmodeBanner.classList.add("d-none");
      if (submitLabelEl) submitLabelEl.textContent = translateKey("cart_submit_default") || "Voir ma commande";
      if (cartBar) cartBar.classList.remove("mf-cart-addmode");
      resetCart();
    }

    if (addmodeCancelBtn) {
      addmodeCancelBtn.addEventListener("click", exitAddMode);
    }

    var recapModalEl = document.getElementById("order-recap-modal");
    var recapBody = document.getElementById("order-recap-body");
    var recapTitle = document.getElementById("order-recap-title");
    var recapConfirmBtn = document.getElementById("order-recap-confirm");
    var recapModal = (recapModalEl && typeof bootstrap !== "undefined")
      ? new bootstrap.Modal(recapModalEl) : null;

    function renderRecap() {
      var cart = updateCartUI();

      if (recapTitle) {
        recapTitle.textContent = addModeOrderId
          ? "#" + addModeOrderId + " — " + (translateKey("recap_total_added") || "Total ajouté")
          : (translateKey("recap_title_default") || "Récapitulatif de votre commande");
      }

      var totalLabel = addModeOrderId
        ? (translateKey("recap_total_added") || "Total ajouté")
        : (translateKey("recap_total") || "Total");

      var html = '<ul class="list-group list-group-flush mb-3">';
      cart.lines.forEach(function (line) {
        html += '<li class="list-group-item d-flex justify-content-between px-0">' +
          '<span>' + line.qty + ' × ' + line.name + '</span>' +
          '<span class="fw-semibold">' + formatNumber(line.qty * line.price) + ' ' + currency + '</span>' +
          '</li>';
      });
      html += '</ul><div class="d-flex justify-content-between fw-bold fs-5">' +
        '<span>' + totalLabel + '</span>' +
        '<span>' + formatNumber(cart.total) + ' ' + currency + '</span></div>';

      var upsell = computeUpsellSuggestions(cart.ids);
      if (upsell.length > 0) {
        var upsellTitle = translateKey("upsell_title") || "Vous pourriez aussi aimer";
        var addLabel = translateKey("upsell_add_btn") || "Ajouter";
        html += '<div class="mf-upsell-section">' +
          '<div class="mf-upsell-title"><i class="bi bi-stars"></i>' + upsellTitle + '</div>';
        upsell.forEach(function (sug) {
          html += '<div class="mf-upsell-item">' +
            '<span><span class="mf-upsell-name">' + sug.name + '</span>' +
            '<span class="mf-upsell-price">' + formatNumber(sug.price) + ' ' + currency + '</span></span>' +
            '<button type="button" class="mf-upsell-add-btn" data-suggest-id="' + sug.id + '">' +
            '<i class="bi bi-plus-lg me-1"></i>' + addLabel + '</button>' +
            '</div>';
        });
        html += '</div>';
      }

      recapBody.innerHTML = html;
    }

    if (recapBody) {
      recapBody.addEventListener("click", function (e) {
        var btn = e.target.closest(".mf-upsell-add-btn");
        if (!btn) return;
        var suggestId = parseInt(btn.dataset.suggestId, 10);
        incrementItemQty(suggestId);
        renderRecap();
      });
    }

    if (submitBtn && recapModal) {
      submitBtn.addEventListener("click", function (e) {
        e.preventDefault();
        var cart = updateCartUI();
        if (cart.count === 0) return;
        renderRecap();
        recapModal.show();
      });

      recapConfirmBtn.addEventListener("click", function () {
        var labelEl = recapConfirmBtn.querySelector(".confirm-label");
        var spinnerEl = recapConfirmBtn.querySelector(".spinner-border");
        recapConfirmBtn.disabled = true;
        if (labelEl) labelEl.textContent = translateKey("cart_sending") || "Envoi…";
        if (spinnerEl) spinnerEl.classList.remove("d-none");

        var targetUrl = addModeOrderId
          ? "/m/" + slug + "/commande/" + addModeOrderId + "/ajouter"
          : form.action;

        fetch(targetUrl, {
          method: "POST",
          headers: { "X-Requested-With": "fetch" },
          body: new FormData(form)
        })
          .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
          .then(function (res) {
            recapModal.hide();
            recapConfirmBtn.disabled = false;
            if (labelEl) labelEl.textContent = translateKey("cart_submit_confirm") || "Confirmer l'envoi";
            if (spinnerEl) spinnerEl.classList.add("d-none");

            var data = res.data;

            if (!data.ok) {
              if (data.locked) {
                showToast(data.error, true);
                exitAddMode();
                refreshMyOrders();
                return;
              }
              showToast(data.error || "Une erreur est survenue.", true);
              return;
            }

            if (addModeOrderId) {
              showToast("Plats ajoutés à la commande #" + data.id + ".", false);
              exitAddMode();
            } else {
              addStoredId(data.id);
              resetCart();
              showToast("Commande envoyée ! Suivez-la dans « Mes commandes ».", false);
            }

            refreshMyOrders();

            var offcanvasEl = document.getElementById("my-orders-offcanvas");
            if (offcanvasEl && typeof bootstrap !== "undefined") {
              bootstrap.Offcanvas.getOrCreateInstance(offcanvasEl).show();
            }
          })
          .catch(function () {
            recapConfirmBtn.disabled = false;
            if (labelEl) labelEl.textContent = translateKey("cart_submit_confirm") || "Confirmer l'envoi";
            if (spinnerEl) spinnerEl.classList.add("d-none");
            showToast("Impossible d'envoyer la commande. Vérifiez votre connexion.", true);
          });
      });
    }

    function statusLabel(status) {
      return translateKey("status_" + status) || status;
    }

    function cancelOrder(orderId, onDone) {
      fetch("/m/" + slug + "/commande/" + orderId + "/annuler-client", {
        method: "POST",
        headers: { "X-Requested-With": "fetch" }
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            showToast(translateKey("cancel_order_success") || "Commande annulée.", false);
          } else if (data.locked) {
            showToast(translateKey("cancel_order_locked") || "Trop tard : cette commande est déjà en préparation.", true);
          } else {
            showToast(data.error || "Impossible d'annuler cette commande.", true);
          }
          refreshMyOrders();
          if (onDone) onDone();
        })
        .catch(function () {
          showToast("Impossible d'annuler la commande. Vérifiez votre connexion.", true);
          if (onDone) onDone();
        });
    }

    function renderMyOrder(order) {
      var itemsHtml = order.items.map(function (it) {
        return "<li>" + it.quantity + " × " + it.name + "</li>";
      }).join("");
      var noteHtml = order.notes ? '<p class="text-secondary small mb-2">📝 ' + order.notes + "</p>" : "";
      var detailUrl = "/m/" + slug + "/commande/" + order.id;

      var actionsHtml = "";
      if (order.editable) {
        actionsHtml =
          '<div class="mf-myorder-actions">' +
            '<button type="button" class="btn btn-outline-secondary btn-sm mf-myorder-addbtn mf-addto-btn" ' +
            'data-order-id="' + order.id + '" data-table-label="' + (order.table_label || "") + '">' +
            '<i class="bi bi-plus-lg me-1"></i>' + (translateKey("add_dishes_btn") || "Ajouter des plats") + '</button>' +
            '<button type="button" class="btn btn-outline-danger btn-sm mf-myorder-cancelbtn mf-cancel-order-trigger" ' +
            'data-order-id="' + order.id + '">' +
            '<i class="bi bi-x-circle me-1"></i>' + (translateKey("cancel_order_btn") || "Annuler la commande") + '</button>' +
          '</div>';
      }

      return (
        '<div class="card mf-myorder-card" data-order-id="' + order.id + '">' +
          '<div class="card-body">' +
            '<div class="d-flex justify-content-between align-items-start mb-2">' +
              '<span class="badge mf-status-badge mf-status-' + order.status + '">' + statusLabel(order.status) + '</span>' +
              '<span class="small text-secondary">#' + order.id + '</span>' +
            '</div>' +
            '<ul class="mf-myorder-items">' + itemsHtml + '</ul>' +
            noteHtml +
            '<div class="d-flex justify-content-between align-items-center mb-2">' +
              '<span class="mf-myorder-total">' + formatNumber(order.total) + ' ' + currency + '</span>' +
              '<a href="' + detailUrl + '" class="small">' + (translateKey("order_detail_link") || "Détail complet →") + '</a>' +
            '</div>' +
            actionsHtml +
          '</div>' +
        '</div>'
      );
    }

    function updateBadge(activeCount) {
      if (!myOrdersBadge) return;
      if (activeCount > 0) {
        myOrdersBadge.textContent = activeCount;
        myOrdersBadge.classList.remove("d-none");
      } else {
        myOrdersBadge.classList.add("d-none");
      }
    }

    function refreshMyOrders() {
      if (!myOrdersList) return;
      var ids = getStoredIds();
      if (ids.length === 0) {
        if (myOrdersEmpty) myOrdersEmpty.style.display = "";
        updateBadge(0);
        return;
      }
      fetch("/m/" + slug + "/commandes/etats.json?ids=" + ids.join(","))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var orders = data.orders || [];
          if (orders.length === 0) {
            if (myOrdersEmpty) myOrdersEmpty.style.display = "";
            myOrdersList.innerHTML = "";
            updateBadge(0);
            return;
          }
          if (myOrdersEmpty) myOrdersEmpty.style.display = "none";
          myOrdersList.innerHTML = orders.slice().reverse().map(renderMyOrder).join("");

          var activeCount = orders.filter(function (o) {
            return o.status !== "servie" && o.status !== "annulee";
          }).length;
          updateBadge(activeCount);
        })
        .catch(function () {});
    }

    if (myOrdersList) {
      myOrdersList.addEventListener("click", function (e) {
        var addBtn = e.target.closest(".mf-addto-btn");
        if (addBtn) {
          enterAddMode(parseInt(addBtn.dataset.orderId, 10), addBtn.dataset.tableLabel);
          return;
        }
        var cancelBtn = e.target.closest(".mf-cancel-order-trigger");
        if (cancelBtn && requestCancelConfirmation) {
          var orderId = parseInt(cancelBtn.dataset.orderId, 10);
          requestCancelConfirmation(function (finish) {
            cancelOrder(orderId, finish);
          });
        }
      });
    }

    updateCartUI();
    refreshMyOrders();
    setInterval(refreshMyOrders, 6000);
  }

  function initCategoryNav() {
    var navLinks = document.querySelectorAll(".mf-nav-pills .nav-link");
    if (!navLinks.length) return;
    var sections = Array.prototype.map.call(navLinks, function (link) {
      return document.querySelector(link.getAttribute("href"));
    });

    function onScroll() {
      var scrollPos = window.scrollY + 120;
      var activeIndex = 0;
      sections.forEach(function (sec, i) {
        if (sec && sec.offsetTop <= scrollPos) activeIndex = i;
      });
      navLinks.forEach(function (link, i) {
        link.classList.toggle("active", i === activeIndex);
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  function initOrderStatusPolling(requestCancelConfirmation) {
    var root = document.getElementById("order-status-root");
    if (!root) return;
    var pollUrl = root.dataset.pollUrl;
    var cancelUrl = root.dataset.cancelUrl;
    var stepOrder = ["nouvelle", "en_preparation", "prete", "servie"];
    var badge = document.getElementById("status-badge");
    var progressBar = document.getElementById("status-progress-bar");
    var stepLabels = document.querySelectorAll(".mf-step-labels span");
    var cancelBtn = document.getElementById("cancel-order-btn");

    function applyStatus(status, label) {
      var translated = (window.MFTranslate && window.MFTranslate("status_" + status)) || label;
      badge.textContent = translated;
      badge.setAttribute("data-i18n", "status_" + status);
      badge.className = "mf-status-badge badge mf-status-" + status;
      var idx = stepOrder.indexOf(status);
      var pct = idx >= 0 ? ((idx + 1) / stepOrder.length) * 100 : 100;
      if (progressBar) progressBar.style.width = pct + "%";
      stepLabels.forEach(function (el, i) {
        el.classList.toggle("active", i <= idx);
      });
    }

    if (cancelBtn && cancelUrl && requestCancelConfirmation) {
      cancelBtn.addEventListener("click", function () {
        requestCancelConfirmation(function (finish) {
          fetch(cancelUrl, { method: "POST", headers: { "X-Requested-With": "fetch" } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              finish();
              if (data.ok) {
                showToast(translateKey("cancel_order_success") || "Commande annulée.", false);
                setTimeout(function () { window.location.reload(); }, 800);
              } else if (data.locked) {
                showToast(translateKey("cancel_order_locked") || "Trop tard : cette commande est déjà en préparation.", true);
                setTimeout(function () { window.location.reload(); }, 800);
              } else {
                showToast(data.error || "Impossible d'annuler cette commande.", true);
              }
            })
            .catch(function () {
              finish();
              showToast("Impossible d'annuler la commande. Vérifiez votre connexion.", true);
            });
        });
      });
    }

    var currentStatus = root.dataset.status;
    applyStatus(currentStatus, root.dataset.statusLabel);

    if (currentStatus === "servie" || currentStatus === "annulee") return;

    var interval = setInterval(function () {
      fetch(pollUrl).then(function (r) { return r.json(); }).then(function (data) {
        if (data.status !== currentStatus) {
          currentStatus = data.status;
          applyStatus(data.status, data.status_label);
          badge.classList.add("mf-pulse");
          if (currentStatus === "servie" || currentStatus === "en_preparation") {
            setTimeout(function () { window.location.reload(); }, 1200);
          }
        }
        if (currentStatus === "servie" || currentStatus === "annulee") {
          clearInterval(interval);
        }
      }).catch(function () {});
    }, 5000);
  }

  function initReviewForm() {
    var card = document.getElementById("review-card");
    if (!card) return;
    var submitUrl = card.dataset.submitUrl;
    var submitBtn = document.getElementById("review-submit-btn");
    var commentEl = document.getElementById("review-comment");

    card.querySelectorAll(".mf-star-picker").forEach(function (picker) {
      var hiddenInput = picker.querySelector("input[type=hidden]");
      var stars = Array.prototype.slice.call(picker.querySelectorAll(".mf-star"));

      function setRating(value) {
        hiddenInput.value = value;
        stars.forEach(function (star) {
          var starValue = parseInt(star.dataset.value, 10);
          star.classList.toggle("bi-star-fill", starValue <= value);
          star.classList.toggle("bi-star", starValue > value);
          star.classList.toggle("mf-star-filled", starValue <= value);
        });
      }

      stars.forEach(function (star) {
        star.addEventListener("click", function () {
          setRating(parseInt(star.dataset.value, 10));
        });
      });
    });

    if (submitBtn) {
      submitBtn.addEventListener("click", function () {
        var formData = new FormData();
        card.querySelectorAll(".mf-star-picker input[type=hidden]").forEach(function (input) {
          formData.append(input.name, input.value);
        });
        if (commentEl) formData.append("comment", commentEl.value);

        submitBtn.disabled = true;
        fetch(submitUrl, { method: "POST", body: formData })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ok) {
              var thanksText = (window.MFTranslate && window.MFTranslate("review_thanks")) || "Merci pour votre avis !";
              card.innerHTML = '<div class="card-body p-4 text-center" style="color: var(--mf-accent-dark);">' +
                '<i class="bi bi-check-circle-fill me-2"></i>' + thanksText + '</div>';
            } else {
              submitBtn.disabled = false;
              showToast(data.error || "Impossible d'enregistrer votre avis.", true);
            }
          })
          .catch(function () {
            submitBtn.disabled = false;
            showToast("Impossible d'enregistrer votre avis. Vérifiez votre connexion.", true);
          });
      });
    }
  }

  function initSortable() {
    if (typeof Sortable === "undefined") return;

    var catList = document.getElementById("categories-list");
    if (catList) {
      Sortable.create(catList, {
        handle: ".mf-drag-handle",
        animation: 180,
        onEnd: function () {
          var order = Array.prototype.map.call(
            catList.querySelectorAll(".mf-category-block"),
            function (el) { return el.dataset.id; }
          );
          fetch(catList.dataset.reorderUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order: order })
          });
        }
      });
    }

    document.querySelectorAll(".mf-items-tbody").forEach(function (tbody) {
      Sortable.create(tbody, {
        handle: ".mf-drag-handle",
        animation: 180,
        onEnd: function () {
          var order = Array.prototype.map.call(
            tbody.querySelectorAll("tr"),
            function (el) { return el.dataset.id; }
          );
          fetch(tbody.dataset.reorderUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order: order, category_id: tbody.dataset.categoryId })
          });
        }
      });
    });
  }

  var kanbanRefreshFn = null;

  function initKitchenPolling() {
    var root = document.getElementById("kanban-root");
    if (!root) return;
    var feedUrl = root.dataset.feedUrl;
    var currency = root.dataset.currency || "";
    var knownIds = new Set(
      Array.prototype.map.call(root.querySelectorAll("[data-order-id]"),
        function (el) { return el.dataset.orderId; })
    );

    var columnLabels = {
      nouvelle: { next: "Marquer en préparation" },
      en_preparation: { next: "Marquer prête" },
      prete: { next: "Marquer servie" }
    };

    function renderCard(order, nextLabel, isNew) {
      var itemsHtml = order.items.map(function (it) {
        return "<li>" + it.quantity + " × " + it.name + "</li>";
      }).join("");
      var noteHtml = order.notes ? '<p class="mf-order-note mb-2">📝 ' + order.notes + "</p>" : "";
      return (
        '<div class="card mf-order-card-k mb-2 ' + (isNew ? "mf-animate-in mf-pulse" : "") + '" data-order-id="' + order.id + '">' +
          '<div class="card-body p-3">' +
            '<div class="d-flex justify-content-between align-items-center mb-2">' +
              '<strong>' + order.table_label + '</strong>' +
              '<span class="mf-elapsed">' + order.elapsed_minutes + ' min</span>' +
            '</div>' +
            '<ul class="mb-2 ps-3 small">' + itemsHtml + '</ul>' +
            noteHtml +
            '<p class="fw-bold mb-2" style="color: var(--mf-accent-dark);">' +
              order.total.toLocaleString("fr-FR") + ' ' + currency +
            '</p>' +
            '<div class="d-flex gap-2">' +
              '<button class="btn btn-outline-secondary btn-sm flex-grow-1 mf-advance-btn" data-order-id="' + order.id + '">' + nextLabel + '</button>' +
              '<button class="btn btn-outline-danger btn-sm mf-cancel-btn" data-order-id="' + order.id + '"><i class="bi bi-x-lg"></i></button>' +
            '</div>' +
          '</div>' +
        '</div>'
      );
    }

    function attachActions(container) {
      container.querySelectorAll(".mf-advance-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          fetch("/admin/commandes/" + btn.dataset.orderId + "/avancer", {
            method: "POST", headers: { "X-Requested-With": "fetch" }
          }).then(refresh);
        });
      });
      container.querySelectorAll(".mf-cancel-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          fetch("/admin/commandes/" + btn.dataset.orderId + "/annuler", {
            method: "POST", headers: { "X-Requested-With": "fetch" }
          }).then(refresh);
        });
      });
    }

    function refresh() {
      fetch(feedUrl).then(function (r) { return r.json(); }).then(function (data) {
        ["nouvelle", "en_preparation", "prete"].forEach(function (status) {
          var col = document.getElementById("col-" + status);
          if (!col) return;
          var orders = data.orders.filter(function (o) { return o.status === status; });
          var countEl = document.getElementById("count-" + status);
          if (countEl) countEl.textContent = orders.length;

          if (orders.length === 0) {
            col.innerHTML = '<p class="text-muted small">Aucune commande.</p>';
            return;
          }
          col.innerHTML = orders.map(function (o) {
            var isNew = !knownIds.has(String(o.id));
            return renderCard(o, columnLabels[status].next, isNew);
          }).join("");
        });

        knownIds = new Set(data.orders.map(function (o) { return String(o.id); }));
        attachActions(root);
      }).catch(function () {});
    }

    function refreshWithCancelAnimation(orderId) {
      var card = root.querySelector('[data-order-id="' + orderId + '"]');
      if (card) {
        card.classList.add("mf-cancelling");
        setTimeout(refresh, 380);
      } else {
        refresh();
      }
    }

    attachActions(root);
    refresh();
    setInterval(refresh, 20000);

    kanbanRefreshFn = refresh;
    window.MFKanbanRefreshWithCancelAnimation = refreshWithCancelAnimation;
  }

  function initCopyButtons() {
    document.querySelectorAll(".mf-copy-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = btn.dataset.copyText;
        if (!text) return;
        navigator.clipboard.writeText(text).then(function () {
          var original = btn.innerHTML;
          btn.innerHTML = '<i class="bi bi-check-lg"></i> Copié';
          setTimeout(function () { btn.innerHTML = original; }, 1500);
        });
      });
    });
  }

  function initPaymentModal() {
    var modalEl = document.getElementById("payment-modal");
    if (!modalEl) return;
    var form = document.getElementById("payment-modal-form");
    var nameEl = document.getElementById("payment-modal-restaurant-name");

    modalEl.addEventListener("show.bs.modal", function (event) {
      var btn = event.relatedTarget;
      if (!btn) return;
      form.action = btn.dataset.actionUrl;
      nameEl.textContent = btn.dataset.restaurantName;
    });
  }

  function initThemeLiveSync() {
    var el = document.getElementById("theme-sync-root") || document.getElementById("public-theme-sync-root");
    if (!el || typeof EventSource === "undefined") return;

    var streamUrl = el.dataset.streamUrl;
    var isAdminChannel = el.id === "theme-sync-root";
    var liveIndicator = document.getElementById("live-indicator");
    var reconnectDelay = 2000;

    function setLiveStatus(isLive) {
      if (!liveIndicator) return;
      liveIndicator.classList.toggle("mf-live-off", !isLive);
      liveIndicator.innerHTML = isLive
        ? '<span class="mf-live-dot"></span> En direct'
        : '<span class="mf-live-dot"></span> Reconnexion…';
    }

    function connect() {
      var source = new EventSource(streamUrl);

      source.onopen = function () {
        setLiveStatus(true);
        reconnectDelay = 2000;
      };

      source.onmessage = function (event) {
        var payload;
        try { payload = JSON.parse(event.data); } catch (e) { return; }

        if (payload.type === "theme_changed") {
          applyThemeVars(payload.data);
          if (isAdminChannel) showToast("🎨 Le thème a été mis à jour.", false);
          return;
        }

        if (!isAdminChannel) return;

        var tableInfo = (payload.data && payload.data.table_label) ? " · " + payload.data.table_label : "";

        if (payload.type === "new_order") {
          playNotificationSound();
          showToast("🆕 Nouvelle commande reçue" + tableInfo + " !", false);
          if (kanbanRefreshFn) kanbanRefreshFn();
        } else if (payload.type === "order_updated") {
          playNotificationSound();
          showToast("✏️ Une commande a été mise à jour" + tableInfo + ".", false);
          if (kanbanRefreshFn) kanbanRefreshFn();
        } else if (payload.type === "order_cancelled") {
          showToast("🚫 Commande annulée par le client" + tableInfo + ".", false);
          var orderId = payload.data && payload.data.id;
          if (orderId && window.MFKanbanRefreshWithCancelAnimation) {
            window.MFKanbanRefreshWithCancelAnimation(orderId);
          } else if (kanbanRefreshFn) {
            kanbanRefreshFn();
          }
        } else if (kanbanRefreshFn) {
          kanbanRefreshFn();
        }
      };

      source.onerror = function () {
        setLiveStatus(false);
        source.close();
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
      };
    }

    connect();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var requestCancelConfirmation = initCancelOrderModal();

    initI18n();
    initToasts();
    initConfirmModal();
    initRestaurantSwitcherModal();
    initOrderCartAndMyOrders(requestCancelConfirmation);
    initCategoryNav();
    initOrderStatusPolling(requestCancelConfirmation);
    initReviewForm();
    initSortable();
    initKitchenPolling();
    initCopyButtons();
    initPaymentModal();
    initThemeLiveSync();
  });
})();
