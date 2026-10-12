// Self-hosted pure computations; effects/proof admission remain in host adapters.
export function plannerKernels(engine) {
  const words = (value, limit) => {
    if (!(value instanceof Uint32Array) || value.length > limit) throw Error('bounded Uint32Array required');
    return value;
  };
  return {
    async score(features, weights, options = {}) {
      words(features, 8 * 1048576); words(weights, 8);
      if (features.length % 8 || weights.length !== 8) throw Error('eight features and weights required');
      const rows = Uint32Array.from({length: features.length / 8}, (_, i) => i);
      return engine.pipeline({steps: [{id: 'result', call: 'score', args: [rows, features, weights]}], outputs: ['result']}, options);
    },
    async evaluate(inputs, instructions, options = {}) {
      words(inputs, 1048576); words(instructions, 512);
      if (instructions.length !== 512) throw Error('256 padded instructions required');
      for (let i = 0; i < 512; i += 2) if (instructions[i] > 2) throw Error('unknown word VM opcode');
      return engine.pipeline({steps: [{id: 'result', call: 'evaluate', args: [inputs, instructions]}], outputs: ['result']}, options);
    }
  };
}
