const state = { images: [], selected: new Map(), lightboxIndex: 0, stage: "choose_edits", lazy: false, email: "" };
state.galleryId = "";
state.galleries = [];
state.sent = new Map();
state.busy = false;
let lazyLoader = null;
let lightboxRequest = 0;
const $ = (selector) => document.querySelector(selector);
const form = $("#gallery-form");
const gallery = $("#gallery");
const tray = $("#tray");
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function animateWorkflow(stage) {
  const order = {choose_edits: 0, wait_for_edits: 1, final_edits: 2};
  const steps = [...document.querySelectorAll(".flow-step")];
  const lines = [...document.querySelectorAll(".flow-line")];
  const target = order[stage];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  steps.forEach((step) => step.classList.remove("complete", "current", "touring"));
  lines.forEach((line) => line.classList.remove("complete", "touring"));
  $("#client-space").hidden = false;
  $("#client-space").scrollIntoView({behavior: reducedMotion ? "auto" : "smooth", block: "center"});

  if (!reducedMotion) await wait(250);
  for (let index = 0; index <= target; index += 1) {
    if (index > 0) {
      lines[index - 1].classList.add("touring");
      if (!reducedMotion) await wait(260);
      lines[index - 1].classList.remove("touring");
      lines[index - 1].classList.add("complete");
    }
    steps[index].classList.add("touring");
    if (!reducedMotion) await wait(430);
    steps[index].classList.remove("touring");
    steps[index].classList.add(index === target ? "current" : "complete");
  }
  if (!reducedMotion) await wait(350);
}

function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timeout = setTimeout(() => reject(new Error("Image load timed out.")), 45000);
    image.onload = () => {
      clearTimeout(timeout);
      resolve();
    };
    image.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("Image could not be loaded."));
    };
    image.decoding = "async";
    image.src = url;
  });
}

async function preloadGallery(images) {
  if (!images.length) return;
  let nextIndex = 0;
  let loaded = 0;
  const failed = [];
  const workerCount = Math.min(10, images.length);
  $("#loader-count").textContent = `Loading photographs 0 / ${images.length}`;
  $("#progress-bar").style.width = "10%";

  async function worker() {
    while (nextIndex < images.length) {
      const index = nextIndex;
      nextIndex += 1;
      try {
        await preloadImage(images[index].thumbnail);
      } catch (error) {
        failed.push(images[index].name);
      }
      loaded += 1;
      $("#loader-count").textContent = `Loading photographs ${loaded} / ${images.length}`;
      $("#progress-bar").style.width = `${10 + (loaded / images.length) * 90}%`;
    }
  }

  await Promise.all(Array.from({length: workerCount}, worker));
  if (failed.length) {
    throw new Error(`${failed.length} photograph${failed.length === 1 ? "" : "s"} could not load. Please try again.`);
  }
}

function message(target, text = "", type = "") {
  target.textContent = text;
  target.classList.remove("error", "success");
  if (type) target.classList.add(type);
}

function applySelections(selections) {
  const images = new Map(state.images.map(image => [image.id, image]));
  state.selected = new Map((selections.saved || []).map(file => [file.id, images.get(file.id) || file]));
  state.sent = new Map((selections.sent || []).map(file => [file.id, images.get(file.id) || file]));
  state.stage = selections.stage || state.stage;
  document.body.dataset.stage = state.stage;
  const current = {choose_edits: 0, wait_for_edits: 1, final_edits: 2}[state.stage];
  document.querySelectorAll('.flow-step').forEach((step, index) => {
    step.classList.toggle('current', index === current);
    step.classList.toggle('complete', index < current);
  });
  document.querySelectorAll('.flow-line').forEach((line, index) => line.classList.toggle('complete', index < current));
  updateTray();
  state.images.forEach(updateSelectionUI);
}

function updateTray() {
  const isFinal = state.stage === "final_edits";
  const files = [...state.selected.values()];
  const sent = isFinal ? [] : [...state.sent.values()];
  $("#selected-count").textContent = files.length + sent.length;
  tray.hidden = files.length + sent.length === 0;
  function row(file, alreadySent) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = file.name;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = alreadySent ? "Unsend" : "Remove";
    button.setAttribute("aria-label", `${button.textContent} ${file.name}`);
    button.disabled = state.busy;
    button.addEventListener("click", () => alreadySent ? mutateSelections("unsend", {file_id: file.id}) : toggleSelection(file));
    li.append(name, button);
    return li;
  }
  $("#selected-list").replaceChildren(...files.map(file => row(file, false)));
  $("#sent-list").replaceChildren(...sent.map(file => row(file, true)));
  $("#saved-heading").textContent = isFinal ? "For download" : "Saved - not sent";
  $("#saved-heading").hidden = files.length === 0;
  $("#sent-heading").hidden = sent.length === 0;
  $("#unsend-hint").hidden = sent.length === 0;
  $("#cart-choose-more").hidden = state.stage !== "wait_for_edits";
  $("#cart-choose-more").disabled = state.busy;
  $("#selected-preview").textContent = isFinal ? "View download cart" : "View edit cart";
  $("#submit-selection").textContent = isFinal ? "Download selected" : "Send saved selections";
  $("#submit-selection").hidden = files.length === 0;
  $("#submit-selection").disabled = state.busy;
}

function updateSelectionUI(image) {
  const isSent = state.stage !== "final_edits" && state.sent.has(image.id);
  const isSelected = state.selected.has(image.id) || isSent;
  const destination = state.stage === "final_edits" ? "download cart" : "edits";
  const label = isSent ? `${image.name} is sent; manage in cart` : `${isSelected ? "Remove" : "Add"} ${image.name} ${isSelected ? "from" : "to"} ${destination}`;
  const card = gallery.querySelector(`[data-image-id="${CSS.escape(String(image.id))}"]`);
  if (card) {
    card.classList.toggle("selected", isSelected);
    card.classList.toggle("sent", isSent);
    const button = card.querySelector(".select-button");
    button.textContent = isSelected ? "\u2713" : "+";
    button.disabled = state.busy;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-pressed", String(isSelected));
  }
  if (state.images[state.lightboxIndex]?.id === image.id) {
    const button = $("#lightbox-select");
    button.classList.toggle("selected", isSelected);
    button.disabled = state.busy;
    button.setAttribute("aria-pressed", String(isSelected));
    const status = $("#lightbox-status");
    status.textContent = isSent ? "SENT FOR EDITING" : isSelected ? "SELECTED" : "NOT SELECTED";
    status.dataset.status = isSent ? "sent" : isSelected ? "selected" : "unselected";
    button.firstChild.textContent = isSent ? "View sent photo in cart " : isSelected ? "Remove selection " : "Select photo ";
    button.querySelector("span").textContent = isSelected ? "\u2713" : "+";
  }
}

function setSelectionBusy(busy) {
  state.busy = busy;
  form.querySelector("button").disabled = busy;
  $("#change-gallery").disabled = busy;
  $("#choose-more").disabled = busy;
  updateTray();
  state.images.forEach(updateSelectionUI);
}

async function mutateSelections(action, payload = {}) {
  if (state.busy) return;
  setSelectionBusy(true);
  let reopenGallery = false;
  message($("#submit-message"));
  message($("#selection-message"), action === "save" || action === "remove" ? "Saving your selection..." : "Updating your edit list...");
  try {
    const response = await fetch("/api/selections", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: state.email, gallery_id: state.galleryId, action, ...payload})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Your changes could not be saved.");
    applySelections(data.selections);
    reopenGallery = state.stage === "choose_edits" && $("#gallery-section").hidden;
    message($("#selection-message"), data.message, "success");
    message($("#submit-message"), data.message, "success");
    if (action === "send") {
      $("#lightbox").close();
      $("#gallery-section").hidden = true;
      $("#stage-message").hidden = false;
      await animateWorkflow("wait_for_edits");
    }
  } catch (error) {
    message($("#selection-message"), error.message, "error");
    message($("#submit-message"), error.message, "error");
  } finally {
    setSelectionBusy(false);
    if (reopenGallery) await loadGallery(state.email, state.galleryId);
  }
}

function toggleSelection(image) {
  if (state.busy) return;
  if (state.stage !== "final_edits" && state.sent.has(image.id)) {
    $("#lightbox").close();
    tray.classList.add("open");
    $("#tray-toggle").setAttribute("aria-expanded", "true");
    $("#sent-list button")?.focus();
    return;
  }
  if (state.stage !== "final_edits") {
    return state.selected.has(image.id) ? mutateSelections("remove", {file_id: image.id}) : mutateSelections("save", {files: [{id: image.id}]});
  }
  if (state.selected.has(image.id)) state.selected.delete(image.id);
  else state.selected.set(image.id, image);
  updateSelectionUI(image);
  updateTray();
}

function openLightbox(index) {
  const requestId = ++lightboxRequest;
  state.lightboxIndex = (index + state.images.length) % state.images.length;
  const image = state.images[state.lightboxIndex];
  $("#lightbox-image").src = image.thumbnail;
  $("#lightbox-image").alt = image.name;
  if (image.previewUrl) {
    const preview = new Image();
    preview.id = 'lightbox-image';
    preview.alt = image.name;
    preview.decoding = 'async';
    preview.fetchPriority = 'high';
    preview.onload = () => {
      if (requestId === lightboxRequest && state.images[state.lightboxIndex] === image) {
        // Reuse the decoded element so no second full-image request is made.
        $("#lightbox-image").replaceWith(preview);
      }
    };
    preview.onerror = () => {
      if (requestId === lightboxRequest && state.images[state.lightboxIndex] === image) $("#lightbox-caption").textContent = `${image.name} — Full-quality photo couldn't load. Close and reopen to retry.`;
    };
    preview.src = image.previewUrl;
  }
  $("#lightbox-caption").textContent = `${state.lightboxIndex + 1} / ${state.images.length} — ${image.name}`;
  updateSelectionUI(image);
  if (!$("#lightbox").open) $("#lightbox").showModal();
}

function createLazyLoader() {
  const queue = [];
  let active = 0;
  let stopped = false;
  const pump = () => {
    while (!stopped && active < 3 && queue.length) {
      const img = queue.shift();
      active += 1;
      img.closest('.photo').classList.remove('load-error');
      let finished = false;
      const finish = (failed) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        img.onload = img.onerror = null;
        img.closest('.photo').classList.toggle('load-error', failed);
        active -= 1;
        pump();
      };
      const timer = setTimeout(() => finish(true), 180000);
      img.onload = () => finish(false);
      img.onerror = () => finish(true);
      img.src = img.dataset.source;
    }
  };
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        queue.push(entry.target);
      }
    });
    pump();
  }, {rootMargin: '150px'});
  return {
    observe: img => observer.observe(img),
    retry: img => { queue.push(img); pump(); },
    stop: () => { stopped = true; queue.length = 0; observer.disconnect(); }
  };
}

function renderGallery() {
  lazyLoader?.stop();
  lazyLoader = state.lazy ? createLazyLoader() : null;
  gallery.replaceChildren(...state.images.map((image, index) => {
    const card = document.createElement("div");
    card.className = "photo";
    card.dataset.imageId = image.id;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `View ${image.name}`);
    const img = document.createElement("img");
    if (state.lazy) img.dataset.source = image.thumbnail;
    else img.src = image.thumbnail;
    img.alt = image.name;
    img.loading = state.lazy ? "eager" : "lazy";
    const select = document.createElement("button");
    select.type = "button";
    select.className = "select-button";
    select.textContent = "+";
    select.setAttribute("aria-label", `Add ${image.name} to edits`);
    select.setAttribute("aria-pressed", "false");
    select.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleSelection(image);
    });
    const name = document.createElement("span");
    name.className = "photo-name";
    name.textContent = image.name;
    card.append(img, select, name);
    if (state.lazy) {
      const retry = document.createElement('button');
      retry.className = 'photo-retry';
      retry.type = 'button';
      retry.textContent = 'Photo could not load. Retry';
      retry.addEventListener('click', event => {
        event.stopPropagation();
        card.classList.remove('load-error');
        lazyLoader.retry(img);
      });
      card.append(retry);
      lazyLoader.observe(img);
    }
    card.addEventListener("click", () => openLightbox(index));
    card.addEventListener("keydown", (event) => {
      if (event.target === card && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        openLightbox(index);
      }
    });
    return card;
  }));
}

function showGalleryPicker() {
  if (state.busy) return;
  state.sent.clear();
  lazyLoader?.stop();
  state.selected.clear();
  state.images = [];
  state.galleryId = "";
  lightboxRequest += 1;
  updateTray();
  tray.classList.remove("open");
  $("#tray-toggle").setAttribute("aria-expanded", "false");
  message($("#submit-message"));
  $("#client-space").hidden = true;
  $("#gallery-section").hidden = true;
  $("#intro").classList.add("compact");
  $("#gallery-choices").replaceChildren(...state.galleries.map((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "gallery-choice";
    const title = document.createElement("span");
    title.className = "choice-title";
    title.textContent = item.gallery;
    const date = document.createElement("span");
    date.className = "choice-date";
    date.textContent = item.date;
    const number = document.createElement("span");
    number.className = "choice-number";
    number.textContent = String(index + 1).padStart(2, "0");
    number.setAttribute("aria-hidden", "true");
    const arrow = document.createElement("span");
    arrow.className = "choice-arrow";
    arrow.textContent = "\u2197";
    arrow.setAttribute("aria-hidden", "true");
    button.append(number, title, date, arrow);
    button.addEventListener("click", () => loadGallery(state.email, item.id));
    return button;
  }));
  $("#gallery-picker").hidden = false;
  $("#picker-title").focus();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadGallery($("#email").value);
});
$("#change-gallery").addEventListener("click", showGalleryPicker);
async function chooseMoreEdits() {
  if (state.busy) return;
  tray.classList.remove("open");
  $("#tray-toggle").setAttribute("aria-expanded", "false");
  await loadGallery(state.email, state.galleryId, true);
}
$("#choose-more").addEventListener("click", chooseMoreEdits);
$("#cart-choose-more").addEventListener("click", chooseMoreEdits);

async function loadGallery(email, galleryId = "", chooseMore = false) {
  if (state.busy) return;
  message($("#form-message"));
  const button = form.querySelector("button");
  button.disabled = true;
  document.body.classList.add("gallery-loading");
  $("#loader").hidden = false;
  $("#loader-count").textContent = "Finding your place...";
  $("#progress-bar").style.width = "28%";
  button.firstElementChild.textContent = "Loading…";
  try {
    const response = await fetch("/api/gallery", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email, gallery_id: galleryId, choose_more: chooseMore}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load the gallery.");
    if (!data.gallery) {
      state.email = email;
      state.galleries = data.galleries || [];
      showGalleryPicker();
      return;
    }
    if (!data.lazy) await preloadGallery(data.images);
    state.galleries = data.galleries || [];
    state.galleryId = data.gallery.id;
    $("#gallery-picker").hidden = true;
    $("#welcome-title").textContent = data.gallery.gallery;
    $("#welcome-date").textContent = data.gallery.date;
    $("#welcome-date").hidden = !data.gallery.date;
    $("#change-gallery").hidden = state.galleries.length < 2;
    message($("#submit-message"));
    gallery.classList.remove("list-view");
    document.querySelectorAll("#view-toggle button").forEach(item => {
      item.classList.toggle("active", item.dataset.view === "grid");
      item.setAttribute("aria-pressed", String(item.dataset.view === "grid"));
    });
    $("#loader-count").textContent = "Your gallery is ready.";
    $("#progress-bar").style.width = "100%";
    await wait(180);
    $("#loader").hidden = true;
    document.body.classList.remove("gallery-loading");
    state.images = data.images;
    state.lazy = Boolean(data.lazy);
    state.email = email;
    lazyLoader?.stop();
    state.stage = data.stage;
    state.selected.clear();
    state.sent.clear();
    if (data.stage !== "final_edits") applySelections(data.selections || {});
    message($("#selection-message"));
    updateTray();
    document.body.dataset.stage = data.stage;
    $("#intro").classList.add("compact");
    await animateWorkflow(data.stage);
    if (data.stage === "wait_for_edits" && !chooseMore) {
      $("#gallery-section").hidden = true;
      $("#stage-message").hidden = false;
      $("#client-space").scrollIntoView({behavior: "smooth"});
      return;
    }
    $("#stage-message").hidden = true;
    renderGallery();
    state.images.forEach(updateSelectionUI);
    updateTray();
    const isFinal = data.stage === "final_edits";
    $("#gallery-eyebrow").textContent = isFinal ? "Your finished gallery" : "Your proofs";
    $("#gallery-title").textContent = isFinal ? "Your final photographs." : "Choose your favorites.";
    $("#view-toggle").hidden = false;
    $("#gallery-count").textContent = `${data.count} photograph${data.count === 1 ? "" : "s"}`;
    $("#gallery-section").hidden = false;
    $("#client-space").scrollIntoView({behavior: "smooth"});
  } catch (error) {
    message($("#form-message"), error.message, "error");
  } finally {
    $("#loader").hidden = true;
    $("#progress-bar").style.width = "0";
    document.body.classList.remove("gallery-loading");
    button.disabled = false;
    button.firstElementChild.textContent = "Open gallery";
  }
}

$("#tray-toggle").addEventListener("click", () => {
  tray.classList.toggle("open");
  $("#tray-toggle").setAttribute("aria-expanded", tray.classList.contains("open"));
});
$("#submit-selection").addEventListener("click", async () => {
  const button = $("#submit-selection");
  button.disabled = true;
  message($("#submit-message"));
  if (state.stage === "final_edits") {
    [...state.selected.values()].forEach((file, index) => {
      setTimeout(() => {
        const link = document.createElement("a");
        link.href = file.downloadUrl;
        link.download = file.name;
        link.target = "_blank";
        link.rel = "noopener";
        link.click();
      }, index * 250);
    });
    message($("#submit-message"), "Your downloads have started.", "success");
    button.disabled = false;
    return;
  }
  await mutateSelections("send", {files: [...state.selected.values()].map(file => ({id: file.id}))});

});
document.querySelectorAll("#view-toggle button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("#view-toggle button").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", String(item === button));
  });
  gallery.classList.toggle("list-view", button.dataset.view === "list");
}));
$("#lightbox-close").addEventListener("click", () => $("#lightbox").close());
$("#lightbox-prev").addEventListener("click", () => openLightbox(state.lightboxIndex - 1));
$("#lightbox-next").addEventListener("click", () => openLightbox(state.lightboxIndex + 1));
$("#lightbox-select").addEventListener("click", () => toggleSelection(state.images[state.lightboxIndex]));
$("#lightbox").addEventListener("click", (event) => { if (event.target === $("#lightbox")) $("#lightbox").close(); });
document.addEventListener("keydown", (event) => {
  if (!$("#lightbox").open) return;
  if (event.key === "ArrowLeft") openLightbox(state.lightboxIndex - 1);
  if (event.key === "ArrowRight") openLightbox(state.lightboxIndex + 1);
});
