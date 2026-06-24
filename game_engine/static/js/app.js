/**
 * app.js — Root Vue application. Load LAST.
 */
const loadingState = { visible: false, text: '' };
function showLoading(m) { loadingState.visible = true; loadingState.text = m || 'Loading...'; }
function updateLoading(m) { loadingState.text = m || ''; }
function hideLoading() { loadingState.visible = false; }

// Global enter functions (used by innerHTML onclick handlers in HomePage)
let _appProxy = null;
window.enterDM = function(name) { if (_appProxy) _appProxy.enterDm(name); };
window.enterPlayer = function(name) { if (_appProxy) _appProxy.enterPlayer(name); };

const app = Vue.createApp({
  data() { return { page: 'home', game: '', role: 'dm' }; },
  methods: {
    enterDm(name) { this.game = name; this.role = 'dm'; this.page = 'game'; },
    enterPlayer(name) { this.game = name; this.role = 'player'; this.page = 'game'; },
  },
});

function reg(name, c) {
  if (c) app.component(name, c);
  else console.warn('Component not loaded:', name);
}
reg('loading-overlay', {
  template: '<div class="loading-overlay" :class="{ show: s.visible }"><div class="spinner"></div><div class="loading-text">{{ s.text }}</div></div>',
  data() { return { s: loadingState }; },
});
reg('home-page', typeof HomePage !== 'undefined' ? HomePage : null);
reg('game-view', typeof GameView !== 'undefined' ? GameView : null);
reg('dm-panel', typeof DmPanel !== 'undefined' ? DmPanel : null);
reg('status-bar', typeof StatusBar !== 'undefined' ? StatusBar : null);
reg('room-sidebar', typeof RoomSidebar !== 'undefined' ? RoomSidebar : null);
reg('chat-area', typeof ChatArea !== 'undefined' ? ChatArea : null);
reg('player-panel', typeof PlayerPanel !== 'undefined' ? PlayerPanel : null);

app.config.globalProperties.$loading = { show: showLoading, update: updateLoading, hide: hideLoading };

const vm = app.mount('#app');
_appProxy = vm;
