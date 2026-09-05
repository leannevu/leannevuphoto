const state = { images: [], selected: new Map(), lightboxIndex: 0, stage: "choose_edits", lazy: false, email: "" };
let lazyLoader = null;
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
  target.className = `message ${type}`;
}

function updateTray() {
  const files = [...state.selected.values()];
  $("#selected-count").textContent = files.length;
  tray.hidden = files.length === 0;
  $("#selected-list").replaceChildren(...files.map((file) => {
    const li = document.createElement("li");
    li.textContent = file.name;
    return li;
  }));
  $("#selected-preview").textContent = state.stage === "final_edits" ? "View download cart" : "View filenames";
  $("#submit-selection").innerHTML = state.stage === "final_edits" ? "Download selected <span aria-hidden=\"true\">↓</span>" : "Add to edits <span aria-hidden=\"true\">→</span>";
}

function updateSelectionUI(image) {
  const isSelected = state.selected.has(image.id);
  const card = gallery.querySelector(`[data-image-id="${CSS.escape(String(image.id))}"]`);
  if (card) {
    card.classList.toggle("selected", isSelected);
    const button = card.querySelector(".select-button");
    button.textContent = isSelected ? "✓" : "+";
    const destination = state.stage === "final_edits" ? "download cart" : "edits";
    button.setAttribute("aria-label", `${isSelected ? "Remove" : "Add"} ${image.name} ${isSelected ? "from" : "to"} ${destination}`);
    button.setAttribute("aria-pressed", String(isSelected));
  }
  if (state.images[state.lightboxIndex]?.id === image.id) {
    const button = $("#lightbox-select");
    button.classList.toggle("selected", isSelected);
    button.setAttribute("aria-pressed", String(isSelected));
    const destination = state.stage === "final_edits" ? "download cart" : "edits";
    button.firstChild.textContent = isSelected ? `Added to ${destination} ` : `Add to ${destination} `;
    button.querySelector("span").textContent = isSelected ? "✓" : "+";
  }
}

function toggleSelection(image) {
  if (state.selected.has(image.id)) state.selected.delete(image.id);
  else state.selected.set(image.id, image);
  updateSelectionUI(image);
  updateTray();
}

function openLightbox(index) {
  state.lightboxIndex = (index + state.images.length) % state.images.length;
  const image = state.images[state.lightboxIndex];
  $("#lightbox-image").src = image.thumbnail;
  $("#lightbox-image").alt = image.name;
  if (image.previewUrl) {
    const preview = new Image();
    preview.onload = () => {
      if (state.images[state.lightboxIndex] === image) $("#lightbox-image").src = preview.src;
    };
    preview.onerror = () => {
      if (state.images[state.lightboxIndex] === image) $("#lightbox-caption").textContent = `${image.name} — Larger preview couldn't load. Close and reopen to retry.`;
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message($("#form-message"));
  const button = form.querySelector("button");
  button.disabled = true;
  document.body.classList.add("gallery-loading");
  $("#loader").hidden = false;
  $("#loader-count").textContent = "Finding your place...";
  $("#progress-bar").style.width = "28%";
  button.firstElementChild.textContent = "Loading…";
  try {
    const email = $("#email").value;
    const response = await fetch("/api/gallery", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load the gallery.");
    if (!data.lazy) await preloadGallery(data.images);
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
    updateTray();
    document.body.dataset.stage = data.stage;
    $("#intro").classList.add("compact");
    await animateWorkflow(data.stage);
    if (data.stage === "wait_for_edits") {
      $("#gallery-section").hidden = true;
      $("#stage-message").hidden = false;
      $("#client-space").scrollIntoView({behavior: "smooth"});
      return;
    }
    $("#stage-message").hidden = true;
    renderGallery();
    updateTray();
    const isFinal = data.stage === "final_edits";
    $("#gallery-eyebrow").textContent = isFinal ? "Your finished gallery" : "Your proofs";
    $("#gallery-title").textContent = isFinal ? "Your final photographs." : "Choose your favorites.";
    $("#view-toggle").hidden = !isFinal;
    $("#gallery-count").textContent = `${data.count} photograph${data.count === 1 ? "" : "s"}`;
    $("#gallery-section").hidden = false;
    $("#gallery-section").scrollIntoView({behavior: "smooth"});
  } catch (error) {
    message($("#form-message"), error.message, "error");
  } finally {
    $("#loader").hidden = true;
    $("#progress-bar").style.width = "0";
    document.body.classList.remove("gallery-loading");
    button.disabled = false;
    button.firstElementChild.textContent = "Open gallery";
  }
});

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
  try {
    const response = await fetch("/api/submit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: state.email, files: [...state.selected.values()]})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not submit your edits.");
    message($("#submit-message"), data.message, "success");
  } catch (error) {
    message($("#submit-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll("#view-toggle button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("#view-toggle button").forEach((item) => item.classList.toggle("active", item === button));
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
