(function () {
  var videos = document.querySelectorAll('[data-card-video]');
  if (!videos.length) return;

  var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function pauseVideo(video) {
    video.pause();
    video.removeAttribute('src');
    var source = video.querySelector('source');
    if (source) {
      video.dataset.src = source.getAttribute('src');
      source.removeAttribute('src');
    }
  }

  function playVideo(video) {
    if (prefersReducedMotion) return;

    var source = video.querySelector('source');
    if (source && !source.getAttribute('src') && video.dataset.src) {
      source.setAttribute('src', video.dataset.src);
      video.load();
    }

    var playPromise = video.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function () {});
    }
  }

  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          var video = entry.target;
          if (entry.isIntersecting) {
            playVideo(video);
          } else {
            pauseVideo(video);
          }
        });
      },
      { rootMargin: '50px', threshold: 0.25 }
    );

    videos.forEach(function (video) {
      observer.observe(video);
    });
  } else {
    videos.forEach(playVideo);
  }
})();
