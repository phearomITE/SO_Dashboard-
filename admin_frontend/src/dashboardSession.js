export async function restoreSession() {
  return { authenticated: true, role: 'admin' };
}

export async function sessionFetch(path, options = {}) {
  return fetch(path, { ...options, credentials: 'same-origin' });
}
