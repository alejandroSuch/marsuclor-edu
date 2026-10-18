<script>
document.querySelectorAll('.podcast-player').forEach((player) => {
  const audio = player.querySelector('audio');
  player.querySelector('.podcast-speed').addEventListener('change', (event) => {
    audio.playbackRate = Number(event.target.value);
  });
  player.querySelectorAll('[data-skip]').forEach((button) => {
    button.addEventListener('click', () => {
      const next = audio.currentTime + Number(button.dataset.skip);
      audio.currentTime = Math.max(0, Number.isFinite(audio.duration) ? Math.min(next, audio.duration) : next);
    });
  });
  const chapters = [...player.querySelectorAll('[data-seek]')];
  chapters.forEach((button) => button.addEventListener('click', () => {
    audio.currentTime = Number(button.dataset.seek);
    audio.play();
  }));
  if (chapters.length) {
    const sync = () => {
      let active = -1;
      chapters.forEach((button, index) => {
        if (audio.currentTime >= Number(button.dataset.seek)) active = index;
        button.removeAttribute('aria-current');
      });
      if (active >= 0) chapters[active].setAttribute('aria-current', 'true');
    };
    audio.addEventListener('timeupdate', sync);
    audio.addEventListener('seeked', sync);
    sync();
  }
});
</script>
