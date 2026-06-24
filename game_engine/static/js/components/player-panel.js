/**
 * player-panel.js — Online players with HP + signal buttons (~100 lines)
 * Uses SSE for real-time player status updates.
 */
const PlayerPanel = {
  template: `
  <div class="player-panel">
    <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center">
      <span>Players ({{ players.length }})</span>
      <button class="btn btn-primary btn-small" @click="signalAll" title="Wake all players">▶ All</button>
    </div>
    <div v-for="p in alivePlayers" :key="p.name" class="player-card">
      <div class="player-card-header" style="display:flex;align-items:center;gap:4px">
        <span class="status-dot" :class="p.status"></span>
        <b style="flex:1">{{ p.name }}</b>
        <button v-if="p.name !== 'DM'" class="btn btn-secondary btn-small" @click="signal(p.name)" title="Signal this player's turn">▶</button>
        <span style="color:#888;font-size:11px">{{ p.statusLabel }}</span>
      </div>
      <div v-if="p.level" style="font-size:11px;color:#aaa;margin:2px 0">
        Lv.{{ p.level }} {{ p.race }} {{ p.class }}
      </div>
      <div v-if="p.hp" class="hp-row">
        <span style="font-size:11px">HP {{ p.hp }}</span>
        <span class="hp-bar"><span class="hp-fill" :class="hpClass(p.hp_pct||0)" :style="{ width: (p.hp_pct||0) + '%' }"></span></span>
      </div>
    </div>
    <div v-if="deadPlayers.length" class="panel-title" style="margin-top:12px;color:#e94560">💀 Dead ({{ deadPlayers.length }})</div>
    <div v-for="p in deadPlayers" :key="p.name" class="player-card dead">
      <span class="status-dot dead"></span> <b style="text-decoration:line-through">{{ p.name }}</b>
    </div>
  </div>`,
  props: { game: String },
  emits: ['signal'],
  data() { return { players: [], status: null, timer: null }; },
  async mounted() { if (this.game) { await this.fetch(); this.timer = setInterval(() => this.fetch(), 5000); } },
  beforeUnmount() { if (this.timer) clearInterval(this.timer); },
  watch: {
    game() { if (this.timer) clearInterval(this.timer); if (this.game) { this.fetch(); this.timer = setInterval(() => this.fetch(), 5000); } },
  },
  methods: {
    async fetch() {
      const [pls, st] = await Promise.all([
        apiGet('/api/games/' + this.game + '/players'),
        apiGet('/api/games/' + this.game + '/agents/status'),
      ]);
      this.status = st;
      const deadSet = new Set(st ? (st.dead || []) : []);
      const playerNames = new Set(st ? (st.players || []) : []);
      this.players = [
        { name: 'DM', status: (st && st.running) ? 'active' : 'waiting', statusLabel: st && st.running ? 'DM' : 'Waiting' },
        ...(pls || []).map(p => ({
          ...p, dead: deadSet.has(p.name),
          status: deadSet.has(p.name) ? 'dead' : (playerNames.has(p.name) ? 'active' : 'waiting'),
          statusLabel: deadSet.has(p.name) ? 'Dead' : (playerNames.has(p.name) ? 'Active' : 'Waiting'),
        })),
      ];
    },
    async signal(name) { this.$emit('signal', name); await apiPost('/api/games/' + this.game + '/signal', { target: name }); },
    async signalAll() { this.$emit('signal', 'all'); await apiPost('/api/games/' + this.game + '/signal', { target: 'all' }); },
  },
  computed: {
    alivePlayers() { return this.players.filter(p => !p.dead); },
    deadPlayers() { return this.players.filter(p => p.dead); },
  },
};
