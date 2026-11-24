// Public entry point for the Idle Ops canvas scene.
//
// The implementation is split into engine/ modules (config, animation,
// sprites, room, effects, motion, scene). This file keeps the historical
// import path so world.js does not need to change.

export { WorldScene } from "./engine/scene.js";
export { STATES } from "./engine/animation.js";
export { formatCount } from "./engine/config.js";
