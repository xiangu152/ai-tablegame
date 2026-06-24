/**
 * loading.js — Global loading overlay (~30 lines)
 * Usage: app.config.globalProperties.$loading.show(msg) / .hide()
 */
const LoadingOverlay = {
  template: `
  <div class="loading-overlay" :class="{ show: visible }">
    <div class="spinner"></div>
    <div class="loading-text">{{ text }}</div>
  </div>`,
  data() { return { visible: false, text: '' }; },
  methods: {
    show(msg) { this.text = msg || 'Loading...'; this.visible = true; },
    hide() { this.visible = false; },
    update(msg) { this.text = msg; },
  },
};
