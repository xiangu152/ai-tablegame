/**
 * home-page.js — Game creation & continue screen (~120 lines)
 */
const HomePage = {
  template: `
  <div class="home-page">
    <div class="home-card">
      <h2 style="color:#e94560;margin-bottom:20px">🐉 DND AI Tablegame</h2>
      <h3 style="color:#aaa;font-size:13px;margin:16px 0 8px">▶ New Game</h3>
      <div class="form-row">
        <select v-model="selectedAdv" style="flex:1">
          <option v-for="a in adventures" :value="a.file">{{ a.name }} ({{ a.size_mb }}MB)</option>
        </select>
        <input v-model="gameName" placeholder="Game name (optional)" style="flex:1">
        <input v-model.number="playerCount" type="number" min="2" max="10" style="width:70px;text-align:center" title="Player count">
        <button class="btn btn-primary" @click="create" :disabled="creating">{{ creating ? 'Creating...' : 'Create' }}</button>
      </div>

      <h3 style="color:#aaa;font-size:13px;margin:16px 0 8px">📂 Continue</h3>
      <div v-if="games.length === 0" style="color:#666">No games yet. Create one above.</div>
      <div v-for="g in games" :key="g.name" class="game-row">
        <div>
          <b>{{ g.name }}</b>
          <span style="color:#888;font-size:11px;margin-left:8px">Players: {{ g.players }} | Saves: {{ g.saves }}</span>
        </div>
        <div>
          <template v-if="g.prepared">
            <button class="btn btn-primary btn-small" @click="$emit('enter-dm', g.name)">DM</button>
            <button class="btn btn-secondary btn-small" @click="$emit('enter-player', g.name)">Player</button>
            <button v-if="g.saves > 0" class="btn btn-secondary btn-small" @click="loadGame(g.name)" style="margin-left:4px">Load</button>
          </template>
          <span v-else style="color:#888;font-size:11px">Not prepared</span>
        </div>
      </div>
    </div>
  </div>`,
  emits: ['enter-dm', 'enter-player'],
  data() {
    return {
      adventures: [], games: [],
      selectedAdv: '', gameName: '', playerCount: 4, creating: false,
    };
  },
  async mounted() {
    this.$loading.show('Loading...');
    const [advs, games] = await Promise.all([apiGet('/api/adventures'), apiGet('/api/games')]);
    this.adventures = advs || [];
    if (this.adventures.length) this.selectedAdv = this.adventures[0].file;
    this.games = games || [];
    this.$loading.hide();
  },
  methods: {
    async create() {
      const pdf = this.selectedAdv;
      let name = this.gameName.trim();
      if (!name) {
        const sel = this.adventures.find(a => a.file === pdf);
        name = (sel ? sel.name : 'game').replace(/[^a-zA-Z0-9\\u4e00-\\u9fff_-]/g, '_').slice(0, 50);
      }
      const pc = this.playerCount || 4;
      this.creating = true;
      this.$loading.show('Preparing game...');
      const r = await apiPost('/api/games/create', { game_name: name, pdf_file: pdf, player_count: pc });
      if (!r || r.status !== 'ok') { this.$loading.hide(); this.creating = false; return alert('Create failed'); }
      this.$loading.update('Starting agents...');
      await apiPost('/api/games/' + name + '/agents/start', { player_count: pc });
      // Poll until agents running
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const st = await apiGet('/api/games/' + name + '/agents/status');
        if (st && st.running) { this.$loading.hide(); this.$emit('enter-dm', name); return; }
        this.$loading.update('Starting agents... (' + (i+1)*2 + 's)');
      }
      this.$loading.hide();
      alert('Agents did not start in time. Check server logs.');
      this.creating = false;
    },
    async loadGame(name) {
      this.$loading.show('Loading checkpoint...');
      const r = await apiPost('/api/games/' + name + '/load', {});
      if (!r || r.status !== 'ok') { this.$loading.hide(); return alert('Load failed: ' + (r && r.message || 'unknown')); }
      this.$loading.update('Starting agents...');
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const st = await apiGet('/api/games/' + name + '/agents/status');
        if (st && st.running) { this.$loading.hide(); this.$emit('enter-dm', name); return; }
        this.$loading.update('Starting agents... (' + (i+1)*2 + 's)');
      }
      this.$loading.hide();
      alert('Agents did not start in time.');
    },
  },
};
