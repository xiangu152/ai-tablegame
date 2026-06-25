/**
 * dm-panel.js — DM panel: character list (read-only) + save management
 */
const DmPanel = {
  template: `
  <div>
    <div class="tab-bar">
      <button v-for="t in tabs" :key="t.id" class="tab-btn" :class="{ active: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
    </div>
    <div v-if="tab === 'chars'" class="tab-content">
      <div class="card">
        <h3 style="color:#e94560;margin-bottom:8px">Characters (created by DM Agent)</h3>
        <p style="color:#888;font-size:12px">角色卡由 DM Agent 在游戏中通过 Card 工具自动创建。</p>
      </div>
      <div v-if="players.length === 0" style="color:#666;padding:12px">No characters created yet. Start a game and the DM will create them.</div>
      <div v-for="p in players" :key="p.name" class="char-row">
        <div>
          <b>{{ p.name }}</b> <span style="color:#888">Lv.{{ p.level }} {{ p.race }} {{ p.class }} | HP {{ p.hp }} | AC {{ p.ac }}</span>
          <span class="hp-bar"><span class="hp-fill" :class="hpClass(p.hp_pct)" :style="{ width: p.hp_pct + '%' }"></span></span>
        </div>
        <button class="btn btn-danger btn-small" @click="deleteChar(p.name)">Delete</button>
      </div>
    </div>
    <div v-if="tab === 'saves'" class="tab-content">
      <div class="card">
        <h3 style="color:#e94560;margin-bottom:8px">Save Checkpoint</h3>
        <div class="form-row">
          <input v-model="saveLabel" placeholder="Save label">
          <button class="btn btn-primary" @click="doSave">💾 Save</button>
        </div>
      </div>
    </div>
  </div>`,
  props: { game: String },
  data() {
    return {
      tab: 'chars', tabs: [{id:'chars',label:'Characters'},{id:'saves',label:'Saves'}],
      saveLabel: '', players: [],
    };
  },
  watch: {
    game() { if (this.game) this.fetch(); },
    tab() { if (this.game) this.fetch(); },
  },
  async mounted() { if (this.game) await this.fetch(); },
  methods: {
    async fetch() {
      if (this.tab === 'chars') this.players = await apiGet('/api/games/' + this.game + '/players') || [];
    },
    async deleteChar(name) {
      if (!confirm('Delete ' + name + '?')) return;
      await apiPost('/api/games/' + this.game + '/players/delete', { name });
      await this.fetch();
    },
    async doSave() {
      const label = this.saveLabel.trim() || 'Manual Save';
      this.$loading.show('Saving checkpoint...');
      await apiPost('/api/games/' + this.game + '/save', { label });
      this.$loading.hide();
      this.saveLabel = '';
    },
  },
};
