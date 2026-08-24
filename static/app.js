const state = { images: [], selected: new Map(), lightboxIndex: 0 };
const $ = (selector) => document.querySelector(selector);
const form = $("#gallery-form");
const gallery = $("#gallery");
const tray = $("#tray");

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
}

function updateSelectionUI(image) {
  const isSelected = state.selected.has(image.id);
  const card = gallery.querySelector(`[data-image-id="${CSS.escape(String(image.id))}"]`);
  if (card) {
    card.classList.toggle("selected", isSelected);
    const button = card.querySelector(".select-button");
    button.textContent = isSelected ? "✓" : "+";
    button.setAttribute("aria-label", `${isSelected ? "Remove" : "Add"} ${image.name} ${isSelected ? "from" : "to"} edits`);
    button.setAttribute("aria-pressed", String(isSelected));
  }
  if (state.images[state.lightboxIndex]?.id === image.id) {
    const button = $("#lightbox-select");
    button.classList.toggle("selected", isSelected);
    button.setAttribute("aria-pressed", String(isSelected));
    button.firstChild.textContent = isSelected ? "Added to edits " : "Add to edits ";
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
  $("#lightbox-caption").textContent = `${state.lightboxIndex + 1} / ${state.images.length} — ${image.name}`;
  updateSelectionUI(image);
  if (!$("#lightbox").open) $("#lightbox").showModal();
}

function renderGallery() {
  gallery.replaceChildren(...state.images.map((image, index) => {
    const card = document.createElement("div");
    card.className = "photo";
    card.dataset.imageId = image.id;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `View ${image.name}`);
    const img = document.createElement("img");
    img.src = image.thumbnail;
    img.alt = image.name;
    img.loading = "lazy";
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
    card.addEventListener("click", () => openLightbox(index));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
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
  button.firstElementChild.textContent = "Loading…";
  try {
    const response = await fetch("/api/gallery", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: $("#email").value}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load the gallery.");
    state.images = data.images;
    state.selected.clear();
    renderGallery();
    updateTray();
    $("#gallery-count").textContent = `${data.count} photograph${data.count === 1 ? "" : "s"}`;
    $("#gallery-section").hidden = false;
    $("#gallery-section").scrollIntoView({behavior: "smooth"});
  } catch (error) {
    message($("#form-message"), error.message, "error");
  } finally {
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
  try {
    const response = await fetch("/api/submit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: $("#email").value, files: [...state.selected.values()]})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not submit your edits.");
    message($("#submit-message"), data.message, "success");
  } catch (error) {
    message($("#submit-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});
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
