/**
 * room-sidebar.js — Room list with switch (~40 lines)
 */
const RoomSidebar = {
  template: `
  <div class="room-sidebar">
    <div class="panel-title">Rooms</div>
    <button v-for="r in rooms" :key="r.id" class="room-btn" :class="{ active: r.name === selected }" @click="$emit('select', r.name)">
      {{ roomIcon(r.type) }} {{ r.name }}
    </button>
    <div v-if="gameState.game" class="sidebar-info">
      <div>{{ gameState.game }}</div>
      <div>Session #{{ gameState.session }}</div>
    </div>
  </div>`,
  props: { game: String, selected: String },
  emits: ['select'],
  data() { return { rooms: [], gameState: {} }; },
  watch: {
    game() { if (this.game) this.fetch(); },
  },
  async mounted() { if (this.game) await this.fetch(); },
  methods: {
    async fetch() {
      const [rooms, state] = await Promise.all([
        apiGet('/api/games/' + this.game + '/rooms'),
        apiGet('/api/games/' + this.game + '/state'),
      ]);
      this.rooms = rooms || [];
      this.gameState = state || {};
      if (this.rooms.length && !this.rooms.find(r => r.name === this.selected))
        this.$emit('select', this.rooms[0].name);
    },
  },
};
