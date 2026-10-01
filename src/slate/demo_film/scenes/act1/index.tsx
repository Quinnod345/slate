// Act 1, the kit's placeholder: every shot as a labelled box with its timing and its read, drawn inside the column
// that every 9:16 platform leaves free of its own buttons (12-70% across, 16-60% down), so the placeholder's own
// text passes the safe-zone check. A nested KitProvider gives the placeholder that column as its frame.
import React from 'react';
import {ActRoot, Ground, KitProvider, PlaceholderAct, useFrameSize} from '@slate/kit';
import type {SceneProps} from '@film/data';

export {silent} from './beats';

function SafePlaceholder({act}: {act: number}) {
  const {width, height} = useFrameSize();
  const box = {left: width * 0.12, top: height * 0.16, width: width * 0.58, height: height * 0.44};
  return (
    <div style={{position: 'absolute', inset: 0}}>
      <Ground />
      <div style={{position: 'absolute', ...box, overflow: 'hidden'}}>
        <KitProvider width={box.width} height={box.height}>
          <PlaceholderAct act={act} note="Slate demo" />
        </KitProvider>
      </div>
    </div>
  );
}

export default function Act1({sub, act}: SceneProps) {
  return (
    <ActRoot act={act} sub={sub}>
      <SafePlaceholder act={act} />
    </ActRoot>
  );
}
