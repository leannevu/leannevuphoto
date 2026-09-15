const state = { images: [], selected: new Map(), lightboxIndex: 0, stage: "choose_edits", lazy: false, email: "" };
state.owner = document.body.dataset.photographer === "true";
state.otherPicks = new Set();
state.clientPicks = [];
state.clientSent = [];
state.galleryId = "";
state.galleries = [];
state.sent = new Map();
state.busy = false;
state.loading = false;
state.page = "full";
state.bookmark = null;
state.bookmarkBusy = false;
let lazyLoader = null;
let lightboxRequest = 0;
let filmstripLoader = null;
let filmstripImages = null;
const filmstripButtons = new Map();
const $ = (selector) => document.querySelector(selector);
const form = $("#gallery-form");
const gallery = $("#gallery");
const tray = $("#tray");
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function readApiResponse(response) {
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`The server returned an incomplete or invalid response (HTTP ${response.status}). Please try again shortly.`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("The server returned an invalid response. Please try again shortly.");
  }
  if (!response.ok) throw new Error(data.error || `The request failed (HTTP ${response.status}). Please try again shortly.`);
  return data;
}

async function requestGallery(payload) {
  for (let attempt = 0; attempt < 2; attempt++) {
    let response;
    try {
      response = await fetch("/api/gallery", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      const data = await readApiResponse(response);
      if (data.redirect === '/photographer') return data;
      if (!Array.isArray(data.galleries) || (data.gallery && !Array.isArray(data.images))) {
        throw new Error("The server returned incomplete gallery information. Please try again shortly.");
      }
      return data;
    } catch (error) {
      // Gallery loading is read-only; never retry selection or bookmark writes.
      if (attempt || (response && !response.ok && ![502, 503, 504].includes(response.status))) throw error;
      await wait(800);
    }
  }
}

async function animateWorkflow(stage) {
  const order = {choose_edits: 0, wait_for_edits: 1, final_edits: 2};
  const steps = [...document.querySelectorAll(".flow-step")];
  const lines = [...document.querySelectorAll(".flow-line")];
  const target = order[stage];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  steps.forEach((step) => step.classList.remove("complete", "current", "touring"));
  lines.forEach((line) => line.classList.remove("complete", "touring"));
  $("#client-space").hidden = false;
  window.scrollTo({top: 0, behavior: "instant"});
  $("#welcome-title").focus({preventScroll: true});

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
  refreshGalleryPage();
}

function updateTray() {
  const isFinal = state.stage === "final_edits" && !state.owner;
  const files = [...state.selected.values()];
  const sent = isFinal ? [] : [...state.sent.values()];
  $("#selected-count").textContent = files.length + sent.length;
  tray.hidden = state.page === 'other' || files.length + sent.length === 0;
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
    const preview = state.images.find(image => image.id === file.id);
    if (preview?.thumbnail) {
      const thumbnail = document.createElement('img');
      thumbnail.src = preview.thumbnail; thumbnail.alt = ''; thumbnail.loading = 'lazy';
      li.append(thumbnail);
    }
    li.append(name, button);
    return li;
  }
  $("#selected-list").replaceChildren(...files.map(file => row(file, false)));
  $("#sent-list").replaceChildren(...sent.map(file => row(file, true)));
  $("#saved-heading").textContent = isFinal ? "For download" : state.owner ? "Photographer picks · saved automatically" : "Saved drafts · not emailed";
  $("#saved-heading").hidden = files.length === 0;
  $("#sent-heading").hidden = sent.length === 0;
  $("#unsend-hint").hidden = sent.length === 0;
  $("#cart-choose-more").hidden = state.stage !== "wait_for_edits";
  $("#cart-choose-more").disabled = state.busy;
  $("#selected-preview").textContent = `${files.length} saved${state.owner || isFinal ? '' : ` · ${sent.length} sent`} · ${tray.classList.contains("open") ? 'Close cart' : 'Review cart'}`;
  $("#submit-selection").textContent = isFinal ? "Download selected" : state.owner ? "Email my photographer picks" : "Email selections to Leanne";
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
  const thumbnail = filmstripButtons.get(image.id);
  if (thumbnail) {
    thumbnail.classList.toggle("selected", isSelected);
    thumbnail.classList.toggle("sent", isSent);
    thumbnail.setAttribute("aria-label", `${image.name}${isSent ? ", sent for editing" : isSelected ? ", selected" : ", not selected"}`);
  }
  if (state.images[state.lightboxIndex]?.id === image.id) {
    const button = $("#lightbox-select");
    button.hidden = state.page === "other";
    button.classList.toggle("selected", isSelected);
    button.disabled = state.busy;
    button.setAttribute("aria-pressed", String(isSelected));
    const status = $("#lightbox-status");
    status.textContent = isSent ? "SENT FOR EDITING" : isSelected ? "SELECTED" : "NOT SELECTED";
    if (state.page === 'other') status.textContent = state.owner ? 'CLIENT PICK' : 'PHOTOGRAPHER PICK';
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
  if (state.busy || state.loading) return;
  setSelectionBusy(true);
  let reopenGallery = false;
  message($("#submit-message"));
  message($("#selection-message"), action === "save" || action === "remove" ? "Saving your selection..." : "Updating your edit list...");
  try {
    const response = await fetch(state.owner ? "/api/photographer/selections" : "/api/selections", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: state.email, gallery_id: state.galleryId, action, ...payload})});
    const data = await readApiResponse(response);
    applySelections(data.selections);
    reopenGallery = state.stage === "choose_edits" && $("#gallery-section").hidden;
    message($("#selection-message"), data.message, "success");
    message($("#submit-message"), data.message, "success");
    if (action === "send" && !state.owner) {
      $("#lightbox").close();
      $("#gallery-section").hidden = true;
      $("#stage-message").hidden = false;
      $("#view-gallery").hidden = true;
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
  if (state.ownerReadOnly) return;
  if (state.busy || state.loading || state.page === "other") return;
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
  refreshGalleryPage();
}

function openLightbox(index) {
  if (!state.images.length) return;
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
  renderFilmstrip();
  updateFilmstripCurrent();
}

function renderFilmstrip() {
  if (filmstripImages === state.images) return;
  filmstripLoader?.stop();
  filmstripImages = state.images;
  filmstripButtons.clear();
  const strip = $("#filmstrip");
  filmstripLoader = createLazyLoader({root: strip, rootMargin: "80px", cardSelector: ".filmstrip-thumb"});
  strip.replaceChildren(...state.images.map((image, index) => ({image, index})).filter(({image}) => visibleOnPage(image)).map(({image, index}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filmstrip-thumb";
    button.dataset.imageId = image.id;
    button.title = image.name;
    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.dataset.source = image.thumbnail;
    const position = document.createElement("span");
    position.className = "filmstrip-number";
    position.textContent = index + 1;
    position.setAttribute("aria-hidden", "true");
    button.append(img, position);
    button.addEventListener("click", () => openLightbox(index));
    filmstripButtons.set(image.id, button);
    filmstripLoader.observe(img);
    return button;
  }));
  state.images.forEach(updateSelectionUI);
}

function updateFilmstripCurrent() {
  const active = state.images[state.lightboxIndex];
  filmstripButtons.forEach((button, id) => {
    button.classList.toggle("current", id === active.id);
    if (id === active.id) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  });
  requestAnimationFrame(() => {
    if (!$("#lightbox").open || $("#filmstrip-panel").hidden) return;
    const strip = $("#filmstrip");
    const button = filmstripButtons.get(state.images[state.lightboxIndex]?.id);
    if (button) strip.scrollTo({left: button.offsetLeft - strip.clientWidth / 2 + button.clientWidth / 2, behavior: "instant"});
  });
}

function createLazyLoader({root = null, rootMargin = "150px", cardSelector = ".photo"} = {}) {
  const queue = [];
  let active = 0;
  let stopped = false;
  const pump = () => {
    while (!stopped && active < 3 && queue.length) {
      const img = queue.shift();
      active += 1;
      img.closest(cardSelector).classList.remove('load-error');
      let finished = false;
      const finish = (failed) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        img.onload = img.onerror = null;
        img.closest(cardSelector).classList.toggle('load-error', failed);
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
  }, {root, rootMargin});
  return {
    observe: img => observer.observe(img),
    retry: img => { queue.push(img); pump(); },
    stop: () => { stopped = true; queue.length = 0; observer.disconnect(); }
  };
}

function renderGallery() {
  $('#photo-number').value = '';
  $('#photo-number').max = String(state.images.length);
  $('#photo-number').removeAttribute('aria-invalid');
  $('#photo-finder-message').textContent = `Enter a photo number from 1 to ${state.images.length} to view and select it.`;
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
  if (state.owner) { showOwnerGalleries().catch(showOwnerError); return; }
  if (state.busy) return;
  window.galleryActivity?.stop();
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
  $("#intro").hidden = true;
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
  $("#picker-title").focus({preventScroll: true});
  window.scrollTo({top: 0, behavior: "instant"});
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadGallery($("#email").value);
});
$("#change-gallery").addEventListener("click", showGalleryPicker);
function scrollToGallery() {
  if ($("#gallery-section").hidden) return;
  $("#gallery-title").focus({preventScroll: true});
  $("#gallery-section").scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start"});
}
$("#view-gallery").addEventListener("click", event => {
  event.preventDefault();
  scrollToGallery();
});
async function chooseMoreEdits() {
  if (state.busy) return;
  tray.classList.remove("open");
  $("#tray-toggle").setAttribute("aria-expanded", "false");
  await loadGallery(state.email, state.galleryId, true);
}
$("#choose-more").addEventListener("click", chooseMoreEdits);
$("#cart-choose-more").addEventListener("click", chooseMoreEdits);

async function loadGallery(email, galleryId = "", chooseMore = false) {
  if (state.busy || state.loading) return;
  state.loading = true;
  message($("#form-message"));
  const button = form.querySelector("button");
  button.disabled = true;
  document.body.classList.add("gallery-loading");
  $("#loader").hidden = false;
  $("#loader-count").textContent = "Finding your place...";
  $("#progress-bar").style.width = "28%";
  button.firstElementChild.textContent = "Loading…";
  try {
    const data = await requestGallery({email, gallery_id: galleryId, choose_more: chooseMore, photographer_mode: state.owner});
    if (data.redirect === '/photographer') {
      window.location.assign(data.redirect);
      return;
    }
    if (!data.gallery) {
      state.email = email;
      state.galleries = data.galleries || [];
      showGalleryPicker();
      return;
    }
    if (!data.lazy) await preloadGallery(data.images);
    state.galleries = data.galleries || [];
    state.galleryId = data.gallery.id;
    state.ownerReadOnly = state.owner && data.gallery.photographer_picks !== 'yes';
    document.body.dataset.ownerReadOnly = String(state.ownerReadOnly);
    state.otherPicks = new Set((state.owner ? [...(data.selections?.client_saved || []), ...(data.selections?.client_sent || [])] : data.selections?.photographer_selected || []).map(file => file.id));
    if (state.owner) {
      state.clientSent = data.selections?.client_sent || [];
      state.clientPicks = [...new Map([...(data.selections?.client_saved || []), ...(data.selections?.client_sent || [])].map(file => [file.id, file])).values()];
      $('#copy-photos-message').textContent = '';
      $('#copy-photos-fallback').hidden = true;
    }
$('#other-picks-tab').hidden = !state.owner && data.gallery.photographer_picks !== 'yes';
    $('#waiting-photographer').hidden = state.owner || data.gallery.photographer_picks !== 'yes';
    $('#other-picks-tab').textContent = state.owner ? 'Client picks' : 'Photographer picks';
    document.querySelector('[data-page="selected"]').textContent = state.owner ? 'My photographer picks' : 'My selected photos';
    $('#change-gallery').textContent = state.owner ? 'Choose another client gallery' : 'Choose another gallery';
    tray.classList.remove("open");
    $("#tray-toggle").setAttribute("aria-expanded", "false");
    $("#gallery-picker").hidden = true;
    $("#welcome-title").textContent = data.gallery.gallery;
    $("#welcome-date").textContent = data.gallery.date;
    $("#welcome-date").hidden = !data.gallery.date;
    $("#change-gallery").hidden = !state.owner && state.galleries.length < 2;
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
    state.page = "full";
    state.bookmark = data.selections?.bookmark || null;
    message($("#bookmark-message"));
    state.lazy = Boolean(data.lazy);
    state.email = email;
    lazyLoader?.stop();
    window.galleryActivity?.open(email, state.galleryId);
    state.stage = data.stage;
    state.selected.clear();
    state.sent.clear();
    if (data.stage !== "final_edits") applySelections(data.selections || {});
    message($("#selection-message"));
    updateTray();
    document.body.dataset.stage = data.stage;
    $("#intro").hidden = true;
    const waiting = data.stage === "wait_for_edits" && !chooseMore;
    $("#stage-message").hidden = !waiting;
    $("#view-gallery").hidden = waiting;
    $("#gallery-section").hidden = true;
    await animateWorkflow(data.stage);
    $(".flow").hidden = state.owner;
    if (data.stage === "wait_for_edits" && !chooseMore) {
      $("#gallery-section").hidden = true;
      $("#stage-message").hidden = false;
      return;
    }
    $("#stage-message").hidden = true;
    renderGallery();
    state.images.forEach(updateSelectionUI);
    updateTray();
    const isFinal = data.stage === "final_edits";
    $("#gallery-eyebrow").textContent = isFinal ? "Your finished gallery" : "Your proofs";
    $("#gallery-title").textContent = state.ownerReadOnly ? "Browse photographs." : isFinal ? "Your final photographs." : state.owner ? "Choose your photographer picks." : "Choose your favorites.";
    $("#view-toggle").hidden = false;
    $("#gallery-count").textContent = `${data.count} photograph${data.count === 1 ? "" : "s"}`;
    $("#gallery-section").hidden = false;
    refreshGalleryPage();
    if (chooseMore) scrollToGallery();
  } catch (error) {
    message($("#form-message"), error.message, "error");
    $("#form-message").scrollIntoView({behavior: "smooth", block: "center"});
  } finally {
    state.loading = false;
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
  updateTray();
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
$("#filmstrip-toggle").addEventListener("click", () => {
  const panel = $("#filmstrip-panel");
  panel.hidden = !panel.hidden;
  $("#lightbox").classList.toggle("filmstrip-hidden", panel.hidden);
  const button = $("#filmstrip-toggle");
  button.setAttribute("aria-expanded", String(!panel.hidden));
  button.setAttribute("aria-label", panel.hidden ? "Show thumbnail filmstrip" : "Hide thumbnail filmstrip");
  button.title = button.getAttribute("aria-label");
  if (!panel.hidden) updateFilmstripCurrent();
});
$("#lightbox").addEventListener("close", () => {
  lightboxRequest += 1;
  filmstripLoader?.stop();
  filmstripImages = null;
  filmstripButtons.clear();
  $("#filmstrip").replaceChildren();
});
$("#lightbox-close").addEventListener("click", () => $("#lightbox").close());
$("#lightbox-prev").addEventListener("click", () => stepLightbox(-1));
$("#lightbox-next").addEventListener("click", () => stepLightbox(1));
$("#lightbox-select").addEventListener("click", () => toggleSelection(state.images[state.lightboxIndex]));
$("#lightbox").addEventListener("click", (event) => { if (event.target === $("#lightbox")) $("#lightbox").close(); });
document.addEventListener("keydown", (event) => {
  if (!$("#lightbox").open) return;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    stepLightbox(event.key === "ArrowLeft" ? -1 : 1);
    if (event.target.closest("#filmstrip")) filmstripButtons.get(state.images[state.lightboxIndex].id)?.focus({preventScroll: true});
  }
});
function isChosen(image) { return image && (state.selected.has(image.id) || (state.stage !== 'final_edits' && state.sent.has(image.id))); }
$('#photo-finder').addEventListener('submit', event => {
  event.preventDefault();
  const input = $('#photo-number');
  const number = Number(input.value);
  if (!Number.isInteger(number) || number < 1 || number > state.images.length) {
    input.setAttribute('aria-invalid', 'true');
    $('#photo-finder-message').textContent = `Enter a whole photo number from 1 to ${state.images.length}.`;
    input.focus();
    return;
  }
  input.removeAttribute('aria-invalid');
  state.page = 'full';
  filmstripImages = null;
  refreshGalleryPage();
  $('#photo-finder-message').textContent = `Photo ${number}: ${state.images[number - 1].name}`;
  const card = gallery.children[number - 1];
  card.focus({preventScroll: true});
  card.scrollIntoView({block: 'center', behavior: 'instant'});
  updateRuler();
});
function stepLightbox(direction) {
  const indices = state.images.flatMap((image, index) => visibleOnPage(image) ? [index] : []);
  if (indices.length) openLightbox(indices[(indices.indexOf(state.lightboxIndex) + direction + indices.length) % indices.length]);
}
function refreshGalleryPage() {
  gallery.classList.toggle('viewing-other', state.page === 'other');
  updateTray();
  let count = 0;
  gallery.querySelectorAll('.photo').forEach(card => {
    const chosen = state.selected.has(card.dataset.imageId) || (state.stage !== 'final_edits' && state.sent.has(card.dataset.imageId));
    if (chosen) count++;
    card.hidden = state.page === 'selected' ? !chosen : state.page === 'other' ? !state.otherPicks.has(card.dataset.imageId) : false;
    card.querySelector('.select-button').hidden = state.page === 'other';
  });
  $('#gallery-empty').hidden = state.page === 'full' || [...gallery.children].some(card => !card.hidden);
  $('#gallery-empty').textContent = state.page === 'other' ? (state.owner ? 'No client picks yet.' : 'Leanne has not selected photographer picks yet.') : 'No selected photos yet. Choose your favorites in the full gallery.';
  $('#picks-description').hidden = state.page !== 'other';
  $('#picks-description').textContent = state.owner ? 'Your client’s saved and sent selections. Your photographer picks are kept separately.' : 'Selected by Leanne. Your own selections are kept separately.';
  $('#bookmark-tools').hidden = state.owner || state.page !== 'full';
  document.querySelectorAll('[data-page]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.page === state.page)));
  if (state.page === 'selected' && $('#lightbox').open) {
    filmstripImages = null;
    if (!count) $('#lightbox').close();
    else if (!isChosen(state.images[state.lightboxIndex])) stepLightbox(1);
    else { renderFilmstrip(); updateFilmstripCurrent(); }
  }
  updateRuler();
}
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => {
  state.page = button.dataset.page;
  filmstripImages = null;
  refreshGalleryPage();
}));
function currentGalleryPhoto() {
  let closest = null, distance = Infinity;
  gallery.querySelectorAll('.photo:not([hidden])').forEach(card => {
    const delta = Math.abs(card.getBoundingClientRect().top - innerHeight * .3);
    if (delta < distance) { closest = card; distance = delta; }
  });
  return closest;
}
function updateRuler() {
  const section = $('#gallery-section'), bounds = section.getBoundingClientRect();
  $('#gallery-ruler').hidden = state.owner || section.hidden || state.page !== 'full' || !state.images.length || bounds.top > innerHeight * .4 || bounds.bottom < 100;
  const current = currentGalleryPhoto();
  const index = state.images.findIndex(image => image.id === current?.dataset.imageId);
  const percent = index < 0 ? 0 : (index + 1) / state.images.length * 100;
  $('#ruler-count').textContent = `${Math.max(0, index + 1)}/${state.images.length}`;
  $('#ruler-progress').style.height = `${percent}%`;
  $('.ruler-track').setAttribute('aria-valuenow', String(Math.round(percent)));
  const markIndex = state.images.findIndex(image => image.id === state.bookmark?.id);
  $('#jump-bookmark').hidden = markIndex < 0;
  $('#clear-bookmark').hidden = !state.bookmark;
  $('#ruler-bookmark').hidden = markIndex < 0;
  $('#ruler-bookmark').style.top = `${(markIndex + 1) / Math.max(1, state.images.length) * 100}%`;
  $('#jump-bookmark').textContent = `Resume at photo ${markIndex + 1}`;
  const atBookmark = markIndex >= 0 && current?.dataset.imageId === state.bookmark.id;
  $('#save-bookmark').textContent = atBookmark ? 'Position saved' : `Save position · photo ${Math.max(1, index + 1)}`;
  $('#ruler-save').textContent = atBookmark ? '✓' : '↧';
  ['#save-bookmark', '#ruler-save'].forEach(id => $(id).disabled = state.bookmarkBusy || !current || atBookmark);
  $('#clear-bookmark').disabled = state.bookmarkBusy;
  gallery.querySelectorAll('.photo').forEach(card => card.classList.toggle('bookmarked', card.dataset.imageId === state.bookmark?.id));
}
async function saveBookmark(clear = false) {
  if (state.bookmarkBusy) return;
  const photo = currentGalleryPhoto();
  if (!clear && !photo) return;
  state.bookmarkBusy = true;
  const galleryId = state.galleryId;
  updateRuler();
  try {
    const response = await fetch('/api/bookmark', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:state.email, gallery_id:galleryId, file_id:clear ? null : photo.dataset.imageId})});
    const data = await readApiResponse(response);
    if (state.galleryId !== galleryId) return;
    state.bookmark = data.bookmark;
    message($('#bookmark-message'), clear ? 'Saved position cleared.' : 'Position saved. Use Resume when you return.');
  } catch (error) { message($('#bookmark-message'), error.message, 'error'); }
  finally { state.bookmarkBusy = false; updateRuler(); }
}
function jumpToBookmark() {
  const card = [...gallery.querySelectorAll('.photo')].find(card => card.dataset.imageId === state.bookmark?.id);
  if (!card) return;
  card.scrollIntoView({block:'center', behavior:'instant'});
  card.focus({preventScroll:true});
  updateRuler();
}
$('#save-bookmark').addEventListener('click', () => saveBookmark());
$('#ruler-save').addEventListener('click', () => saveBookmark());
$('#clear-bookmark').addEventListener('click', () => saveBookmark(true));
$('#jump-bookmark').addEventListener('click', jumpToBookmark);
$('#ruler-bookmark').addEventListener('click', jumpToBookmark);
let rulerFrame = false;
function scheduleRuler() {
  if (rulerFrame) return;
  rulerFrame = true;
  requestAnimationFrame(() => { rulerFrame = false; updateRuler(); });
}
window.addEventListener('scroll', scheduleRuler, {passive:true});
window.addEventListener('resize', scheduleRuler);
new ResizeObserver(scheduleRuler).observe(gallery);

function visibleOnPage(image) {
  return state.page === 'full' || (state.page === 'other' ? state.otherPicks.has(image.id) : isChosen(image));
}
$('#waiting-photographer').addEventListener('click', async () => {
  if (state.busy) return;
  await loadGallery(state.email, state.galleryId, true);
  state.page = 'other';
  filmstripImages = null;
  refreshGalleryPage();
  scrollToGallery();
});
async function showOwnerGalleries() {
  if (state.busy) return;
  const data = await readApiResponse(await fetch('/api/photographer/galleries'));
  message($('#owner-message'));
  $('#owner-retry').hidden = true;
  $('#owner-galleries').hidden = false;
  $('#client-space').hidden = true;
  $('#gallery-section').hidden = true;
  tray.hidden = true;
  $('#owner-choices').replaceChildren(...data.galleries.map(item => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'gallery-choice';
    const title = document.createElement('span'); title.className = 'choice-title';
    title.textContent = item.gallery;
    const detail = document.createElement('span'); detail.className = 'choice-date';
    detail.textContent = `${item.email} · ${item.date}${item.photographer_picks === 'yes' ? '' : ' · Photographer picks disabled'}`;
    button.append(title, detail);
    button.addEventListener('click', async () => {
      $('#owner-galleries').hidden = true;
      await loadGallery(item.email, item.id, true);
    });
    return button;
  }));
}
function showOwnerError(error) {
  $('#owner-galleries').hidden = false;
  message($('#owner-message'), error.message, 'error');
  $('#owner-retry').hidden = false;
}
async function copySelectedPhotoNames(client, sentOnly = false) {
  const status = $('#copy-photos-message');
  const fallback = $('#copy-photos-fallback');
  if (state.busy || state.loading) {
    status.textContent = 'Please wait for your selections to finish saving.';
    return;
  }
  fallback.hidden = true;
  const files = sentOnly ? state.clientSent : client ? state.clientPicks : [...state.selected.values()];
  const names = [...new Set(files.map(file => file.name))];
  if (!names.length) {
    status.textContent = sentOnly ? 'No client sent filenames to copy.' : client ? 'No client selections to copy.' : 'No photographer selections to copy.';
    return;
  }
  const text = `[${names.join(', ')}]`;
  try {
    await navigator.clipboard.writeText(text);
    status.textContent = `Copied ${names.length} ${sentOnly ? 'client sent' : client ? 'client' : 'photographer'} filenames. Ready to paste into the retrieval script.`;
  } catch {
    fallback.value = text;
    fallback.hidden = false;
    fallback.focus();
    fallback.select();
    status.textContent = 'Automatic copying is unavailable. Press Ctrl+C (or Command+C) to copy the selected list.';
  }
}
if (state.owner) {
  $('#copy-client-photos').addEventListener('click', () => copySelectedPhotoNames(true));
  $('#copy-client-sent').addEventListener('click', () => copySelectedPhotoNames(true, true));
  $('#copy-my-photos').addEventListener('click', () => copySelectedPhotoNames(false));
  $('#intro').hidden = true;
  $('#owner-retry').addEventListener('click', () => showOwnerGalleries().catch(showOwnerError));
  showOwnerGalleries().catch(showOwnerError);
}
