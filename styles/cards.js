<script>
const diagramDialog = document.querySelector('.card-diagram-dialog');
let diagramTrigger = null;

diagramDialog?.addEventListener('close', () => {
  diagramTrigger?.focus();
  diagramTrigger = null;
});

document.addEventListener('click', (event) => {
  if (!(event.target instanceof Element)) return;
  const zoom = event.target.closest('.card-zoom');
  if (zoom && diagramDialog) {
    const thumbnail = zoom.querySelector('.card-diagram');
    const enlarged = diagramDialog.querySelector('.card-diagram-large');
    enlarged.src = thumbnail.currentSrc || thumbnail.src;
    enlarged.alt = thumbnail.alt;
    diagramTrigger = zoom;
    diagramDialog.showModal();
    diagramDialog.querySelector('.card-diagram-scroll').scrollTo(0, 0);
    diagramDialog.querySelector('.card-diagram-close').focus();
    return;
  }
  if (event.target.closest('.card-diagram-close')) {
    diagramDialog?.close();
    return;
  }
  const card = event.target.closest('.study-card');
  if (!card) return;
  const flipped = card.getAttribute('aria-pressed') === 'true';
  card.setAttribute('aria-pressed', String(!flipped));
  card.closest('.study-card-wrap')?.classList.toggle('is-flipped', !flipped);
});
</script>
