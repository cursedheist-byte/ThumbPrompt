(() => {
  "use strict";

  const TOPIC_LABELS = {
    gaming: { question: "Which game?", subtitle: "e.g. Minecraft, GTA V, Fortnite, Roblox" },
    documentary: { question: "What's the documentary about?", subtitle: "e.g. Deep sea creatures, a historical event" },
    vlog: { question: "What's this vlog about?", subtitle: "e.g. A day in Tokyo, moving to a new city" },
    challenge: { question: "What's the challenge?", subtitle: "e.g. 24 hours in a box, last to leave wins" },
    entertainment: { question: "What's the topic?", subtitle: "e.g. Celebrity reaction, prank, comedy sketch" },
    tech: { question: "What's the topic/product?", subtitle: "e.g. iPhone 17 review, best budget laptop" },
    educational: { question: "What's the topic?", subtitle: "e.g. How black holes work, learn Python basics" },
  };

  const state = {
    step: 1,
    category: null,
    faceChoice: null,
    faceImageFile: null,
    faceUseMode: null,
    refThumbFile: null,
    specificElementFile: null,
    topic: "",
    title: "",
    specificElementsText: "",
    wantsText: "",
    generationId: null,
    concepts: [],
    selectedConceptIndex: null,
    // Server-determined entitlement (never decided by this JS - always
    // copied verbatim from a usage payload's ads_enabled field, seeded
    // here from the server-rendered snapshot so it's correct even
    // before the first API call). See updateProcessingAdSlots().
    adsEnabled: !!(window.__INITIAL_USAGE__ && window.__INITIAL_USAGE__.ads_enabled),
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function showError(message) {
    const banner = $("#errorBanner");
    banner.textContent = message;
    banner.style.display = "block";
    banner.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function clearError() {
    const banner = $("#errorBanner");
    banner.style.display = "none";
    banner.textContent = "";
  }

  function goToStep(stepNum) {
    state.step = stepNum;
    $$(".wizard-step").forEach((el) => {
      el.style.display = Number(el.dataset.step) === stepNum ? "block" : "none";
    });
    $$(".step-node").forEach((el) => {
      const n = Number(el.dataset.step);
      el.classList.toggle("active", n === stepNum);
      el.classList.toggle("done", n < stepNum);
    });
    clearError();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // -------------------------------------------------------------
  // Step 1: Category
  // -------------------------------------------------------------
  $$("#categoryGrid .option-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$("#categoryGrid .option-card").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      state.category = btn.dataset.value;
      $("#toStep2").disabled = false;
    });
  });

  $("#toStep2").addEventListener("click", () => {
    const isGaming = state.category === "gaming";
    $("#gamingOnlyBlock").style.display = isGaming ? "block" : "none";
    const labels = TOPIC_LABELS[state.category] || TOPIC_LABELS.entertainment;
    $("#topicQuestion").textContent = labels.question;
    $("#topicSubtitle").textContent = labels.subtitle;
    updateStep2ContinueState();
    goToStep(2);
  });

  // -------------------------------------------------------------
  // Step 2: Category-specific details
  // -------------------------------------------------------------
  $$("#faceChoiceGrid .option-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$("#faceChoiceGrid .option-card").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      state.faceChoice = btn.dataset.value;
      $("#faceUploadBlock").style.display = state.faceChoice === "with_face" ? "block" : "none";

      if (state.faceChoice !== "with_face") {
        // Switching away from "With Face": clear the face-use-mode selection
        // and hide the block. We do not touch faceImageFile here so we don't
        // disturb the existing upload/remove behavior.
        state.faceUseMode = null;
        hideFaceUseModeBlock(true);
      } else if (state.faceImageFile) {
        // Switching back to "With Face" with a file already present.
        showFaceUseModeBlock();
      }

      updateStep2ContinueState();
    });
  });

  setupUpload({
    inputId: "faceImageInput",
    previewRowId: "facePreviewRow",
    labelId: "faceUploadLabel",
    defaultLabel: "Click to upload a face/reference photo",
    onChange: (file) => {
      state.faceImageFile = file;
      if (file) {
        showFaceUseModeBlock();
      } else {
        state.faceUseMode = null;
        hideFaceUseModeBlock(true);
      }
      updateStep2ContinueState();
    },
  });

  setupUpload({
    inputId: "refThumbInput",
    previewRowId: "refThumbPreviewRow",
    labelId: "refThumbUploadLabel",
    defaultLabel: "Click to upload a reference thumbnail",
    onChange: (file) => { state.refThumbFile = file; },
  });

  setupUpload({
    inputId: "specificElementInput",
    previewRowId: "specificElementPreviewRow",
    labelId: "specificElementUploadLabel",
    defaultLabel: "Click to upload a reference image of that element (optional)",
    onChange: (file) => { state.specificElementFile = file; },
  });

  $("#topicInput").addEventListener("input", (e) => {
    state.topic = e.target.value.trim();
    updateStep2ContinueState();
  });

  function updateStep2ContinueState() {
    let ok = state.topic.length > 0;
    if (state.category === "gaming") {
      ok = ok && !!state.faceChoice;
      if (state.faceChoice === "with_face") {
        ok = ok && !!state.faceImageFile;
        ok = ok && !!state.faceUseMode;
      }
    }
    $("#toStep3").disabled = !ok;
  }

  $("#toStep3").addEventListener("click", () => goToStep(3));

  // -------------------------------------------------------------
  // Face-use-mode block ("Just A Reactor" / "Put In Game")
  // Injected dynamically after the face preview row so no HTML
  // template changes are required.
  // -------------------------------------------------------------
  function ensureFaceUseModeBlock() {
    let block = $("#faceUseModeBlock");
    if (block) return block;

    block = document.createElement("div");
    block.id = "faceUseModeBlock";
    block.className = "face-use-mode-block";
    block.style.display = "none";
    block.style.marginTop = "16px";
    block.innerHTML = `
      <p class="field-label">How should your face be used?</p>
      <div id="faceUseModeGrid" class="option-grid">
        <button type="button" class="option-card" data-value="reactor">
          <strong>Just A Reactor</strong>
          <div>Keep my face as the creator's face.</div>
        </button>
        <button type="button" class="option-card" data-value="put_in_game">
          <strong>Put In Game</strong>
          <div>Integrate my face into the game's character/world.</div>
        </button>
      </div>
    `;

    const previewRow = $("#facePreviewRow");
    if (previewRow && previewRow.parentNode) {
      previewRow.insertAdjacentElement("afterend", block);
    } else {
      // Fallback: append to the face upload block if the preview row
      // isn't in the DOM yet for some reason.
      const uploadBlock = $("#faceUploadBlock");
      if (uploadBlock) uploadBlock.appendChild(block);
    }

    block.querySelectorAll(".option-card").forEach((btn) => {
      btn.addEventListener("click", () => {
        block.querySelectorAll(".option-card").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        state.faceUseMode = btn.dataset.value;
        updateStep2ContinueState();
      });
    });

    return block;
  }

  function showFaceUseModeBlock() {
    const block = ensureFaceUseModeBlock();
    block.style.display = "block";
  }

  function hideFaceUseModeBlock(resetSelection) {
    const block = $("#faceUseModeBlock");
    if (!block) return;
    block.style.display = "none";
    if (resetSelection) {
      block.querySelectorAll(".option-card").forEach((b) => b.classList.remove("selected"));
    }
  }

  // -------------------------------------------------------------
  // Step 3: Title
  // -------------------------------------------------------------
  $("#titleInput").addEventListener("input", (e) => {
    state.title = e.target.value.trim();
    $("#titleCharCount").textContent = String(e.target.value.length);
    $("#toStep4").disabled = state.title.length === 0;
  });

  $("#toStep4").addEventListener("click", () => goToStep(4));

  // -------------------------------------------------------------
  // Step 4: References + specific elements
  // -------------------------------------------------------------
  $("#specificElementsInput").addEventListener("input", (e) => {
    state.specificElementsText = e.target.value.trim();
  });
  $("#wantsTextInput").addEventListener("input", (e) => {
    state.wantsText = e.target.value.trim();
  });

  $("#toAnalyze").addEventListener("click", runAnalysis);

  // -------------------------------------------------------------
  // Generic upload widget wiring
  // -------------------------------------------------------------
  function setupUpload({ inputId, previewRowId, labelId, defaultLabel, onChange }) {
    const input = $(`#${inputId}`);
    const row = $(`#${previewRowId}`);
    const label = $(`#${labelId}`);
    if (!input) return;

    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;

      if (file.size > 6 * 1024 * 1024) {
        showError("Image is too large. Please choose a file under 6 MB.");
        input.value = "";
        return;
      }

      row.innerHTML = "";
      const wrapper = document.createElement("div");
      wrapper.className = "preview-thumb";
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      const removeBtn = document.createElement("button");
      removeBtn.className = "preview-remove";
      removeBtn.type = "button";
      removeBtn.textContent = "✕";
      removeBtn.addEventListener("click", () => {
        input.value = "";
        row.innerHTML = "";
        label.textContent = defaultLabel;
        onChange(null);
      });
      wrapper.appendChild(img);
      wrapper.appendChild(removeBtn);
      row.appendChild(wrapper);
      label.textContent = file.name;
      onChange(file);
    });
  }

  // -------------------------------------------------------------
  // Step 5: Analyze -> concepts
  // -------------------------------------------------------------
  async function runAnalysis() {
    goToStep(5);
    $("#loadingConcepts").style.display = "flex";
    $("#conceptsContent").style.display = "none";
    // Ad slot's own visibility is independent of the loading container's
    // (it's a child of #loadingConcepts, so it disappears automatically
    // once renderConcepts() hides that container - no explicit "hide ad"
    // step needed on success). This call just makes sure it reflects the
    // most recently known server entitlement right as loading starts.
    updateProcessingAdSlots();

    const formData = new FormData();
    formData.append("category", state.category);
    formData.append("topic", state.topic);
    formData.append("title", state.title);
    if (state.category === "gaming") {
      formData.append("face_choice", state.faceChoice);
      if (state.faceChoice === "with_face") {
        formData.append("face_use_mode", state.faceUseMode);
      }
    }
    formData.append("specific_elements_text", state.specificElementsText);
    formData.append("wants_text", state.wantsText);
    if (state.faceImageFile) formData.append("face_image", state.faceImageFile);
    if (state.refThumbFile) formData.append("reference_thumbnail", state.refThumbFile);
    if (state.specificElementFile) formData.append("specific_element_image", state.specificElementFile);

    try {
      const res = await fetch("/api/analyze", { method: "POST", body: formData });
      const data = await res.json();
      if (!data.ok) {
        goToStep(4);
        showError(data.error || "Something went wrong generating concepts.");
        if (data.usage) updateUsagePill(data.usage);
        return;
      }
      state.generationId = data.generation_id;
      state.concepts = data.concepts;
      state.selectedConceptIndex = null;
      updateUsagePill(data.usage);
      renderConcepts(data.video_understanding, data.concepts);
    } catch (err) {
      goToStep(4);
      showError("Network error while generating concepts. Please try again.");
    }
  }

  function renderConcepts(understanding, concepts) {
    $("#loadingConcepts").style.display = "none";
    $("#conceptsContent").style.display = "block";
    $("#videoUnderstanding").textContent = understanding || "";

    const grid = $("#conceptGrid");
    grid.innerHTML = "";
    concepts.forEach((concept, idx) => {
      const card = document.createElement("div");
      card.className = "concept-card";
      card.dataset.index = String(idx);
      const objects = Array.isArray(concept.important_objects)
        ? concept.important_objects.join(", ")
        : concept.important_objects || "";
      card.innerHTML = `
        <h3>${escapeHtml(concept.concept_name || `Concept ${idx + 1}`)}</h3>
        <p>${escapeHtml(concept.core_visual_idea || "")}</p>
        <p><strong>Emotional hook:</strong> ${escapeHtml(concept.emotional_hook || "")}</p>
        <p><strong>Key objects:</strong> ${escapeHtml(objects)}</p>
        <p><strong>Why it works:</strong> ${escapeHtml(concept.why_it_could_work || "")}</p>
      `;
      card.addEventListener("click", () => {
        $$(".concept-card").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
        state.selectedConceptIndex = idx;
        $("#toFinalize").disabled = false;
      });
      grid.appendChild(card);
    });
  }

  $("#toFinalize").addEventListener("click", runFinalize);

  // -------------------------------------------------------------
  // Step 6: Finalize
  // -------------------------------------------------------------
  async function runFinalize() {
    goToStep(6);
    $("#loadingFinal").style.display = "flex";
    $("#finalContent").style.display = "none";
    // See the matching comment in runAnalysis() above.
    updateProcessingAdSlots();

    const formData = new FormData();
    formData.append("generation_id", state.generationId);
    formData.append("concept_index", String(state.selectedConceptIndex));
    if (state.faceChoice === "with_face" && state.faceUseMode) {
      formData.append("face_use_mode", state.faceUseMode);
    }
    if (state.faceImageFile) formData.append("face_image", state.faceImageFile);
    if (state.refThumbFile) formData.append("reference_thumbnail", state.refThumbFile);
    if (state.specificElementFile) formData.append("specific_element_image", state.specificElementFile);

    try {
      const res = await fetch("/api/finalize", { method: "POST", body: formData });
      const data = await res.json();
      if (!data.ok) {
        goToStep(5);
        showError(data.error || "Something went wrong generating the final prompt.");
        if (data.usage) updateUsagePill(data.usage);
        return;
      }
      updateUsagePill(data.usage);
      renderFinal(data.final_image_prompt, data.structured_breakdown);
    } catch (err) {
      goToStep(5);
      showError("Network error while generating the final prompt. Please try again.");
    }
  }

  function renderFinal(promptText, breakdown) {
    $("#loadingFinal").style.display = "none";
    $("#finalContent").style.display = "block";
    $("#finalPromptText").textContent = promptText || "";

    const list = $("#breakdownList");
    list.innerHTML = "";
    Object.entries(breakdown || {}).forEach(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key.replace(/_/g, " ");
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.appendChild(dt);
      list.appendChild(dd);
    });
  }

  $("#copyPromptBtn").addEventListener("click", async () => {
    const text = $("#finalPromptText").textContent;
    try {
      await navigator.clipboard.writeText(text);
      const btn = $("#copyPromptBtn");
      const original = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = original; }, 1500);
    } catch (err) {
      showError("Could not copy automatically — please select and copy the text manually.");
    }
  });

  $("#regenerateBtn").addEventListener("click", runFinalize);

  $("#startOverBtn").addEventListener("click", () => {
    window.location.reload();
  });

  // -------------------------------------------------------------
  // Back buttons
  // -------------------------------------------------------------
  $$("[data-back]").forEach((btn) => {
    btn.addEventListener("click", () => goToStep(Number(btn.dataset.back)));
  });

  // -------------------------------------------------------------
  // Usage pill + plans modal
  // -------------------------------------------------------------
  function updateUsagePill(usage) {
    if (!usage) return;
    $("#usageLabel").textContent = `${usage.plan_label} · ${usage.prompts_remaining}/${usage.prompt_limit} prompts left`;
    toggleTopupSection(!!usage.topup_available);
    updatePlanCardStates(usage.plan);
    // ads_enabled always comes from the server (Free -> true, active
    // paid plan -> false, expired paid plan -> true once the backend
    // has lazily reverted it to Free - see database.get_user_enforcing_
    // expiry()). This is the ONLY place state.adsEnabled changes after
    // initial page load, and it only ever copies what the server sent.
    if (typeof usage.ads_enabled === "boolean") {
      state.adsEnabled = usage.ads_enabled;
    }
    updateProcessingAdSlots();
  }

  // -------------------------------------------------------------
  // Processing ads (Free / expired-plan users only)
  // -------------------------------------------------------------
  // Shows/hides the two loading-state ad placeholders based purely on
  // state.adsEnabled (itself only ever set from a server usage payload -
  // see updateUsagePill()). Safe to call any time; both slots are no-ops
  // if their element isn't in the DOM.
  function updateProcessingAdSlots() {
    const analyzeSlot = $("#analyzeAdSlot");
    const finalizeSlot = $("#finalizeAdSlot");
    const display = state.adsEnabled ? "block" : "none";
    if (analyzeSlot) analyzeSlot.style.display = display;
    if (finalizeSlot) finalizeSlot.style.display = display;
  }

  function toggleTopupSection(show) {
    const section = $("#topupSection");
    if (section) section.style.display = show ? "block" : "none";
  }

  function updatePlanCardStates(activePlan) {
    $$(".plan-card").forEach((card) => {
      const isActive = card.dataset.planId === activePlan;
      card.classList.toggle("active", isActive);
      const btn = card.querySelector(".buy-plan-btn");
      if (btn) {
        btn.disabled = isActive;
        btn.textContent = isActive ? "Current plan" : btn.dataset.buyLabel;
      }
    });
  }

  $$(".buy-plan-btn").forEach((btn) => {
    // Stash the original "₹9 — Buy" label so updatePlanCardStates() can
    // restore it after temporarily showing "Current plan"/"Processing...".
    btn.dataset.buyLabel = btn.textContent;
  });

  $("#plansBtn").addEventListener("click", () => {
    $("#plansModal").style.display = "flex";
  });
  $("#closePlansModal").addEventListener("click", () => {
    $("#plansModal").style.display = "none";
  });

  // -------------------------------------------------------------
  // Payments (Razorpay)
  // -------------------------------------------------------------
  async function createOrder(body) {
    const res = await fetch("/api/create-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not start checkout.");
    return data.order;
  }

  async function verifyPayment(checkoutResponse) {
    const res = await fetch("/api/payment/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        razorpay_order_id: checkoutResponse.razorpay_order_id,
        razorpay_payment_id: checkoutResponse.razorpay_payment_id,
        razorpay_signature: checkoutResponse.razorpay_signature,
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Payment verification failed.");
    return data.usage;
  }

  function openCheckout(order, description) {
    if (typeof Razorpay === "undefined") {
      showError("Payment checkout could not load. Please check your connection and try again.");
      return;
    }
    const rzp = new Razorpay({
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      order_id: order.order_id,
      name: "ThumbPrompt",
      description: description,
      handler: async (response) => {
        try {
          const usage = await verifyPayment(response);
          updateUsagePill(usage);
          $("#plansModal").style.display = "none";
        } catch (err) {
          showError(err.message || "Payment verification failed. Please contact support if you were charged.");
        }
      },
      modal: {
        // No-op: the user simply closed the checkout without paying.
        // Nothing was charged, nothing to roll back.
        ondismiss: () => {},
      },
      theme: { color: "#5b5bf0" },
    });
    rzp.on("payment.failed", (resp) => {
      const reason = resp && resp.error && resp.error.description
        ? resp.error.description
        : "please try again.";
      showError(`Payment failed: ${reason}`);
    });
    rzp.open();
  }

  $$(".buy-plan-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      const plan = btn.dataset.plan;
      btn.disabled = true;
      btn.textContent = "Processing...";
      try {
        const order = await createOrder({ type: "plan", plan });
        openCheckout(order, `ThumbPrompt — ${plan} plan`);
      } catch (err) {
        showError(err.message || "Could not start checkout.");
      } finally {
        btn.disabled = false;
        btn.textContent = btn.dataset.buyLabel;
      }
    });
  });

  const topupBtn = $("#buyTopupBtn");
  if (topupBtn) {
    topupBtn.addEventListener("click", async () => {
      const qtyInput = $("#topupQuantity");
      const quantity = Math.max(1, parseInt(qtyInput.value, 10) || 1);
      topupBtn.disabled = true;
      try {
        const order = await createOrder({ type: "topup", quantity });
        openCheckout(order, `ThumbPrompt — ${quantity} extra generation${quantity > 1 ? "s" : ""}`);
      } catch (err) {
        showError(err.message || "Could not start checkout.");
      } finally {
        topupBtn.disabled = false;
      }
    });
  }

  // Match the top-up section's initial visibility to the server-rendered
  // usage snapshot (avoids a flash of the wrong state on page load).
  if (window.__INITIAL_USAGE__) {
    toggleTopupSection(!!window.__INITIAL_USAGE__.topup_available);
  }
  updateProcessingAdSlots();

  // -------------------------------------------------------------
  // Utilities
  // -------------------------------------------------------------
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  goToStep(1);
})();
