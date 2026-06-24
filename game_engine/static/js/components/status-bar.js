/**
 * status-bar.js — Game control bar (pause/resume/stop) + round info (~45 lines)
 */
const StatusBar = {
  template: `
  <div class="status-bar">
    <span style="color:#e94560;font-weight:bold;font-size:15px">{{ title }}</span>
    <span v-if="status" class="status-label" :class="status.paused ? 'paused' : 'running'">
      {{ status.paused ? '⏸ Paused' : '▶ Running' }}
    </span>
    <div style="margin-left:auto;display:flex;gap:8px">
      <button v-if="status && status.running" class="btn btn-secondary btn-small" @click="toggle">
        {{ status.paused ? '▶ Resume' : '⏯ Pause' }}
      </button>
      <button v-if="status && status.running" class="btn btn-danger btn-small" @click="stop">⏹ Stop</button>
      <button class="btn btn-secondary btn-small" @click="$emit('back')">← Back</button>
    </div>
  </div>`,
  props: { title: String, game: String },
  emits: ['back'],
  data() { return { status: null, timer: null }; },
  async mounted() { if (this.game) { await this.poll(); this.timer = setInterval(() => this.poll(), 3000); } },
  beforeUnmount() { if (this.timer) clearInterval(this.timer); },
  watch: { game() { if (this.timer) clearInterval(this.timer); if (this.game) { this.poll(); this.timer = setInterval(() => this.poll(), 3000); } } },
  methods: {
    async poll() { this.status = await apiGet('/api/games/' + this.game + '/agents/status'); },
    async toggle() {
      if (this.status.paused) await apiPost('/api/games/' + this.game + '/agents/resume', {});
      else await apiPost('/api/games/' + this.game + '/agents/pause', {});
      await this.poll();
    },
    async stop() {
      if (!confirm('Stop the game?')) return;
      await apiPost('/api/games/' + this.game + '/agents/stop', {});
      await this.poll();
    },
  },
};
