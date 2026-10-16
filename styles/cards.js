<script>
document.addEventListener('click', (event) => {
  const card = event.target.closest('.study-card');
  if (!card) return;
  const flipped = card.getAttribute('aria-pressed') === 'true';
  card.setAttribute('aria-pressed', String(!flipped));
});
</script>
