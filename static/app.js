document.querySelectorAll('[data-dismiss]').forEach((button) => button.addEventListener('click', () => {
  const toast = button.closest('[data-toast]');
  if (toast) {
    toast.classList.add('is-leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }
}));
document.querySelectorAll('[data-toast]').forEach((toast) => {
  window.setTimeout(() => {
    toast.classList.add('is-leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }, 5000);
});
document.querySelectorAll('[data-confirm]').forEach((form) => form.addEventListener('submit', (event) => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); }));
const variants = document.querySelector('#variants');
document.querySelector('.add-variant')?.addEventListener('click', () => { const input = document.createElement('input'); input.name = 'variants'; input.placeholder = 'Variação de texto'; variants.appendChild(input); input.focus(); });
document.querySelectorAll('[data-prepare]').forEach((button) => button.addEventListener('click', async () => { button.disabled = true; try { const response = await fetch(`/publicacoes/${button.dataset.prepare}/preparar`, { method: 'POST' }); const data = await response.json(); await navigator.clipboard?.writeText(data.text); window.open(data.group_reference, '_blank', 'noopener,noreferrer'); button.textContent = 'Preparado'; } catch { button.textContent = 'Tente novamente'; } finally { button.disabled = false; } }));

// Image upload preview
const imageFileInput = document.getElementById('image_file');
const imgPreview = document.getElementById('img-preview');
const previewWrap = document.getElementById('current-image-wrap');
const removeImgBtn = document.getElementById('remove-img-btn');
const removeImageField = document.getElementById('remove_image');
const uploadHint = document.getElementById('upload-hint');

if (imageFileInput) {
  imageFileInput.addEventListener('change', () => {
    const file = imageFileInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      imgPreview.src = e.target.result;
      previewWrap.style.display = '';
      if (uploadHint) uploadHint.textContent = file.name;
      if (removeImageField) removeImageField.value = '0';
    };
    reader.readAsDataURL(file);
  });
}

if (removeImgBtn) {
  removeImgBtn.addEventListener('click', () => {
    imgPreview.src = '';
    previewWrap.style.display = 'none';
    if (imageFileInput) imageFileInput.value = '';
    if (uploadHint) uploadHint.textContent = 'JPG, PNG, GIF ou WEBP · máx 16 MB';
    if (removeImageField) removeImageField.value = '1';
  });
}

// Interval preset buttons
const intervalInput = document.getElementById('interval_seconds_input');
const intervalBadge = document.getElementById('interval-badge');
const intervalForm = intervalInput?.closest('form');

function secondsToLabel(s) {
  s = parseInt(s);
  if (s < 60) return s + (s === 1 ? ' segundo' : ' segundos');
  if (s < 3600) { const m = Math.floor(s/60); return m + (m === 1 ? ' minuto' : ' minutos'); }
  if (s < 86400) { const h = Math.floor(s/3600); return h + (h === 1 ? ' hora' : ' horas'); }
  const d = Math.floor(s/86400); return d + (d === 1 ? ' dia' : ' dias');
}

document.querySelectorAll('.preset-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const seconds = btn.dataset.seconds;
    if (intervalInput) intervalInput.value = seconds;
    // highlight active
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    // update badge preview
    if (intervalBadge) intervalBadge.textContent = secondsToLabel(seconds);
    // auto-submit so change is saved immediately
    if (intervalForm) intervalForm.submit();
  });
});

// Update badge when typing in custom input
if (intervalInput) {
  intervalInput.addEventListener('input', () => {
    const v = parseInt(intervalInput.value);
    if (v >= 5 && intervalBadge) intervalBadge.textContent = secondsToLabel(v);
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  });
}
