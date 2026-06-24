/**
 * game-view.js — Main chat-app layout (~80 lines)
 * Composes: StatusBar, RoomSidebar, ChatArea, PlayerPanel
 */
const GameView = {
  template: `
  <div class="game-layout">
    <status-bar :title="title" :game="game" @back="$emit('back')" />
    <div class="game-body">
      <room-sidebar :game="game" :selected="room" @select="onRoom" />
      <chat-area ref="chat" :game="game" :room="room" @new-message="onMsg" />
      <player-panel :game="game" />
    </div>
  </div>`,
  props: { game: String, role: String },
  emits: ['back'],
  data() { return { room: '酒馆大厅' }; },
  computed: {
    title() { return (this.role === 'dm' ? '🎭 DM Panel' : '🎲 Player') + ' · ' + this.game; },
  },
  methods: {
    onRoom(name) { this.room = name; },
    onMsg(msg) { /* future: dice log update */ },
  },
};
