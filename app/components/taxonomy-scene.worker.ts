/// <reference lib="webworker" />

import {
  buildTaxonomyScene,
  taxonomySceneTransferables,
  type TaxonomySceneNode,
} from "./taxonomy-scene";

type TaxonomySceneRequest = {
  key: string;
  nodes: TaxonomySceneNode[];
};

self.onmessage = (event: MessageEvent<TaxonomySceneRequest>) => {
  const scene = buildTaxonomyScene(event.data.nodes);
  self.postMessage({ key: event.data.key, scene }, { transfer: taxonomySceneTransferables(scene) });
};

export {};
