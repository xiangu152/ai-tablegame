/**
 * player-panel.js — Online players with HP + status dots (~80 lines)
 */
const PlayerPanel = {
  template: `
  <div class="player-panel">
    <div class="panel-title">Players Online ({{ players.length }})</div>
    <div v-for="p in players" :key="p.name" class="player-card" :class="{ dead: p.dead }">
      <div class="player-card-header">
        <span class="status-dot" :class="p.status"></span>
        <b>{{ p.name }}</b>
        <span style="color:#888;font-size:11px;margin-left:auto">{{ p.statusLabel }}</span>
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
  data() { return { players: [], timer: null }; },
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
      const deadSet = new Set(st ? (st.dead || []) : []);
      this.players = (pls || []).map(p => ({
        ...p, dead: deadSet.has(p.name),
        status: deadSet.has(p.name) ? 'dead' : 'active',
        statusLabel: deadSet.has(p.name) ? 'Dead' : 'Active',
      }));
      // Add DM
      this.players.unshift({ name: 'DM', status: (st && st.running) ? 'active' : 'waiting', statusLabel: st && st.running ? 'Active' : 'Waiting' });
    },
  },
  computed: {
    deadPlayers() { return this.players.filter(p => p.dead); },
  },
};
