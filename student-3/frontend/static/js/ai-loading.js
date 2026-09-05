(() => {
  const jobs = new Map();

  function jobFor(element) {
    return element && (element.matches('[data-ai-job]') ? element : element.closest('[data-ai-job]'));
  }

  function controls(job) {
    return job.matches('button') ? [job] : [...job.querySelectorAll('button[type="submit"], button[data-ai-trigger]')];
  }

  function start(job) {
    if (!job || jobs.has(job)) return;
    const indicator = document.getElementById(job.dataset.aiIndicator);
    const elapsed = indicator?.querySelector('[data-ai-elapsed]');
    const slow = indicator?.querySelector('[data-ai-slow]');
    const buttons = controls(job);
    buttons.forEach((button) => {
      button.dataset.aiOriginalLabel ||= button.textContent.trim();
      button.textContent = 'Analysing…';
      button.disabled = true;
    });
    let seconds = 0;
    if (elapsed) elapsed.textContent = '0s';
    if (slow) slow.hidden = true;
    const timer = window.setInterval(() => {
      seconds += 1;
      if (elapsed) elapsed.textContent = `${seconds}s`;
      if (slow && seconds >= 60) slow.hidden = false;
    }, 1000);
    jobs.set(job, { timer, buttons });
  }

  function finish(job) {
    const active = jobs.get(job);
    if (!active) return;
    window.clearInterval(active.timer);
    active.buttons.forEach((button) => {
      button.textContent = button.dataset.aiOriginalLabel || button.textContent;
      button.disabled = false;
    });
    jobs.delete(job);
  }

  function showTransportError(job) {
    const target = document.getElementById(job.dataset.aiResults);
    if (!target) return;
    target.innerHTML = `<div class="alert alert--danger" role="alert"><div><p class="alert__title">AI analysis could not be loaded</p><p class="alert__body">The request failed before results were returned. No data was changed.</p><button class="btn btn-secondary" type="button" data-ai-retry="${job.id}">Try again</button></div></div>`;
  }

  document.body.addEventListener('htmx:beforeRequest', (event) => start(jobFor(event.detail.elt)));
  document.body.addEventListener('htmx:afterRequest', (event) => finish(jobFor(event.detail.elt)));
  document.body.addEventListener('htmx:sendError', (event) => {
    const job = jobFor(event.detail.elt);
    if (job) showTransportError(job);
  });
  document.body.addEventListener('htmx:responseError', (event) => {
    const job = jobFor(event.detail.elt);
    if (job) showTransportError(job);
  });
  document.addEventListener('click', (event) => {
    const retry = event.target.closest('[data-ai-retry]');
    if (!retry) return;
    const job = document.getElementById(retry.dataset.aiRetry);
    if (!job) return;
    if (job.matches('form')) job.requestSubmit();
    else job.click();
  });
})();
