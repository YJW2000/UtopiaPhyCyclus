import './basket-animation.css'

export function animateBasketAddition(trigger: HTMLElement | undefined, destination: HTMLElement | null, added: number) {
  if (!trigger || !destination || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const start = trigger.getBoundingClientRect()
  const end = destination.getBoundingClientRect()
  if (!start.width || !end.width || end.bottom <= 0 || end.top >= window.innerHeight) return

  const x = start.left + start.width / 2
  const y = start.top + start.height / 2
  const dx = end.left + end.width / 2 - x
  const dy = end.top + end.height / 2 - y
  const flight = document.createElement('span')
  flight.className = 'basket-flight'
  flight.setAttribute('aria-hidden', 'true')
  flight.textContent = '+' + added
  flight.style.left = x + 'px'
  flight.style.top = y + 'px'
  document.body.append(flight)

  const duration = 520
  const lift = Math.min(46, Math.abs(dx) / 5, Math.max(0, (y + dy * 0.4) - 24))
  const travel = flight.animate([
    { transform: 'translate(-50%, -50%) scale(.85)', opacity: 0.85 },
    { translate: dx * 0.4 + 'px ' + (dy * 0.4 - lift) + 'px', transform: 'translate(-50%, -50%) scale(1)', opacity: 1, offset: 0.4 },
    { translate: dx + 'px ' + dy + 'px', transform: 'translate(-50%, -50%) scale(.48)', opacity: 0 },
  ], { duration, easing: 'cubic-bezier(.32,.02,.62,1)' })
  travel.finished.then(() => flight.remove(), () => flight.remove())
  destination.animate([
    { transform: 'scale(1)' }, { transform: 'scale(1.25)' }, { transform: 'scale(1)' },
  ], { delay: duration - 80, duration: 240, easing: 'ease-out' })
}
