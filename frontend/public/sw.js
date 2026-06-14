const CACHE_NAME = 'layercut-v1'

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/api/')) return
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const clone = response.clone()
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
        return response
      })
      .catch(() => caches.match(event.request))
  )
})
