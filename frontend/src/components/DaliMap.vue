<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick, watch } from 'vue';
import * as echarts from 'echarts';

const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });
type MapFeature = { type: 'Feature'; properties: Record<string, any>; geometry: any };
type MapCollection = { type: 'FeatureCollection'; features: MapFeature[] };
const node = ref<HTMLElement>();
const expandedNode = ref<HTMLElement>();
const expanded = ref(false);
const ready = ref(false);
const error = ref('');
const mode = ref<'prefecture' | 'city'>('prefecture');
const selected = ref('大理市');
const mapName = 'dali-local-geography';
const aliases: Record<string, string> = {
  '漾濞彝族自治县': '漾濞县', '南涧彝族自治县': '南涧县',
  '巍山彝族回族自治县': '巍山县'
};
let collection: MapCollection;
let chart: echarts.ECharts | undefined;
let expandedChart: echarts.ECharts | undefined;
let observer: ResizeObserver | undefined;
let expandedObserver: ResizeObserver | undefined;
const controller = new AbortController();
const geographicCenter = [100.210651, 25.742713];

function mapOption() {
  const compact = props.compact && !expanded.value;
  return {
    animation: false,
    tooltip: {
      trigger: 'item', confine: true, backgroundColor: '#ffffff',
      borderColor: '#dedbd6', borderWidth: 1, padding: [9, 12],
      textStyle: { color: '#35332f', fontSize: 12 },
      formatter: (params: any) => {
        const feature = collection?.features.find(f => f.properties.name === params.name);
        if (!feature) return '';
        return `<b>${feature.properties.name}</b><br/><span style="color:#76736d">行政区划 · ${feature.properties.adcode}</span>`;
      }
    },
    geo: {
      map: mapName,
      roam: true,
      zoom: mode.value === 'city' ? 2.2 : 1,
      center: mode.value === 'city' ? geographicCenter : undefined,
      layoutCenter: ['50%', '51%'], layoutSize: '92%',
      aspectScale: 0.9, scaleLimit: { min: 0.8, max: 6 },
      selectedMode: false,
      label: {
        show: true, color: '#58625b', fontSize: compact ? 10 : 12,
        formatter: (params: any) => aliases[params.name] || params.name,
        textBorderColor: '#f8f8f4', textBorderWidth: 2
      },
      itemStyle: {
        areaColor: '#e5e9e2', borderColor: '#ffffff', borderWidth: 1.5,
        shadowBlur: 0
      },
      emphasis: {
        label: { color: '#6d2528', fontWeight: 600 },
        itemStyle: { areaColor: '#e6d2cc', borderColor: '#ffffff', borderWidth: 2 }
      },
      regions: collection?.features.map((feature, index) => ({
        name: feature.properties.name,
        itemStyle: { areaColor: feature.properties.name === selected.value ? '#dcb8b5' : index % 3 === 0 ? '#dce3d9' : index % 3 === 1 ? '#e9ece5' : '#e2e7df' },
        label: { color: feature.properties.name === selected.value ? '#76282c' : '#58625b', fontWeight: feature.properties.name === selected.value ? 600 : 400 }
      }))
    }
  };
}

function configure(instance: echarts.ECharts) {
  instance.setOption(mapOption(), true);
  instance.off('click');
  instance.on('click', (params: any) => {
    if (!params.name) return;
    selected.value = params.name;
    for (const current of [chart, expandedChart]) {
      current?.setOption({
        geo: { regions: collection.features.map(feature => ({
          name: feature.properties.name,
          itemStyle: { areaColor: feature.properties.name === selected.value ? '#dcb8b5' : '#e5e9e2' },
          label: { color: feature.properties.name === selected.value ? '#76282c' : '#58625b', fontWeight: feature.properties.name === selected.value ? 600 : 400 }
        })) }
      });
    }
  });
}

function setMode(value: 'prefecture' | 'city') {
  mode.value = value;
  for (const instance of [chart, expandedChart]) if (instance) configure(instance);
}

function zoom(factor: number) {
  const instance = expanded.value ? expandedChart : chart;
  if (!instance) return;
  const current = instance.getOption().geo as any[];
  instance.setOption({ geo: { zoom: Math.min(6, Math.max(0.8, (current?.[0]?.zoom || 1) * factor)) } });
}

function reset() {
  selected.value = '大理市';
  for (const instance of [chart, expandedChart]) if (instance) configure(instance);
}

async function openExpanded() {
  expanded.value = true;
  await nextTick();
}

function mountExpanded() {
  if (!expandedNode.value || !ready.value) return;
  expandedChart = echarts.init(expandedNode.value, undefined, { renderer: 'svg' });
  configure(expandedChart);
  expandedObserver = new ResizeObserver(() => expandedChart?.resize());
  expandedObserver.observe(expandedNode.value);
}

function disposeExpanded() {
  expandedObserver?.disconnect();
  expandedChart?.dispose();
  expandedChart = undefined;
}

onMounted(async () => {
  try {
    const response = await fetch('/maps/dali-counties.geojson', { signal: controller.signal });
    if (!response.ok) throw new Error('地理资源加载失败');
    collection = await response.json() as MapCollection;
    if (collection.type !== 'FeatureCollection' || collection.features.length !== 12) throw new Error('地理资源格式不正确');
    // Preserve the provider geometry. Label anchors are provider-supplied centroids.
    collection = {
      ...collection,
      features: collection.features.map(feature => ({
        ...feature,
        properties: { ...feature.properties, cp: feature.properties.centroid || feature.properties.center }
      }))
    };
    echarts.registerMap(mapName, collection as any);
    if (!node.value || controller.signal.aborted) return;
    chart = echarts.init(node.value, undefined, { renderer: 'svg' });
    configure(chart);
    observer = new ResizeObserver(() => chart?.resize());
    observer.observe(node.value);
    ready.value = true;
  } catch (reason) {
    if (!controller.signal.aborted) error.value = reason instanceof Error ? reason.message : '地图暂不可用';
  }
});

watch(() => props.compact, () => {
  for (const instance of [chart, expandedChart]) if (instance) { configure(instance); instance.resize(); }
});

onBeforeUnmount(() => {
  controller.abort();
  observer?.disconnect();
  chart?.dispose();
  disposeExpanded();
});
</script>

<template>
  <section class="dali-map" :class="{ compact }" data-testid="dali-map" :data-map-ready="ready">
    <div class="map-stage">
      <div ref="node" class="map-canvas" role="img" aria-label="大理白族自治州十二县市真实行政区划地图"></div>
      <div class="map-view-switch" aria-label="地图显示范围">
        <button :class="{ active: mode === 'prefecture' }" @click="setMode('prefecture')">全州</button>
        <button :class="{ active: mode === 'city' }" @click="setMode('city')">大理市</button>
      </div>
      <div class="map-north" aria-hidden="true"><span>北</span><svg viewBox="0 0 20 32"><path d="M10 1L18 26L10 21L2 26Z" fill="currentColor"/><path d="M10 1V21L2 26Z" fill="#c9c9bf"/></svg></div>
      <div class="map-controls" aria-label="地图操作">
        <button @click="zoom(1.25)" title="放大" aria-label="放大地理地图">+</button>
        <button @click="zoom(0.8)" title="缩小" aria-label="缩小地理地图">−</button>
        <button @click="reset" title="复位" aria-label="复位地理地图">↺</button>
        <button @click="openExpanded" title="查看大图" aria-label="展开大理地图">⛶</button>
      </div>
      <div class="map-selected" v-if="ready">
        <span class="selected-dot"></span><strong>{{ selected }}</strong><span>行政区域</span>
      </div>
      <div v-if="error" class="map-feedback"><strong>{{ error }}</strong><span>请检查本地 maps 资源是否完整。</span></div>
      <div v-else-if="!ready" class="map-feedback">正在加载大理地理底图…</div>
    </div>
    <footer class="map-footer">
      <span>地理底图 · 设备坐标待接入</span>
      <a href="https://help.aliyun.com/zh/datav/datav-7-0/user-guide/datav-geoatlas-widgets/" target="_blank" rel="noopener noreferrer">DataV / 高德 · 学习交流</a>
    </footer>
    <el-dialog v-model="expanded" title="大理白族自治州 · 地理底图" width="min(1120px, 94vw)" destroy-on-close @opened="mountExpanded" @closed="disposeExpanded">
      <div class="expanded-toolbar">
        <div class="expanded-tabs"><button :class="{ active: mode === 'prefecture' }" @click="setMode('prefecture')">全州</button><button :class="{ active: mode === 'city' }" @click="setMode('city')">大理市</button></div>
        <div><button @click="zoom(1.25)">放大</button><button @click="zoom(0.8)">缩小</button><button @click="reset">复位</button></div>
      </div>
      <div ref="expandedNode" class="expanded-map" role="img" aria-label="大理白族自治州可缩放行政区划地图"></div>
      <div class="expanded-note">
        <strong>{{ selected }}</strong>
        <span>当前断面未提供设备经纬度，电网连接关系请查看拓扑图。</span>
        <a href="https://help.aliyun.com/zh/datav/datav-7-0/user-guide/datav-geoatlas-widgets/" target="_blank" rel="noopener noreferrer">地图来源：DataV / 高德</a>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.dali-map{--map-height:430px;overflow:hidden;background:var(--surface);border-radius:4px;}
.dali-map.compact{--map-height:330px;}
@media(min-width:951px) and (max-width:1360px){.dali-map.compact{--map-height:280px;}}
.map-stage{position:relative;height:var(--map-height);overflow:hidden;border:1px solid #e6e5de;border-radius:4px;background:#f4f5f0;}
.map-canvas{width:100%;height:100%;touch-action:pan-y;}
.map-view-switch{position:absolute;top:12px;left:12px;display:flex;padding:2px;background:rgba(255,255,255,.97);border:1px solid #deddd5;border-radius:4px;}
.map-view-switch button,.expanded-toolbar button{border:0;border-radius:2px;background:transparent;padding:6px 12px;color:#77756e;font:inherit;font-size:12px;cursor:pointer;transition:background .15s,color .15s;}
.map-view-switch button.active,.expanded-tabs button.active{background:#f2e7e5;color:#87272c;font-weight:600;}
.map-view-switch button:hover,.expanded-toolbar button:hover{background:#f1eee8;}
.map-north{position:absolute;top:16px;right:17px;display:flex;align-items:center;flex-direction:column;gap:2px;color:#757970;font-size:10px;pointer-events:none;}
.map-north svg{width:18px;height:28px;}
.map-controls{position:absolute;right:12px;bottom:14px;display:grid;grid-template-columns:1fr;overflow:hidden;border:1px solid #deddd5;border-radius:4px;background:#ffffffed;}
.map-controls button{display:grid;place-items:center;width:29px;height:28px;border:0;border-bottom:1px solid #ebe9e3;background:transparent;color:#66675f;font-size:17px;cursor:pointer;}
.map-controls button:last-child{border-bottom:0;font-size:20px;}
.map-controls button:hover{background:#f2e7e5;color:#87272c;}
.map-controls button:focus-visible,.map-view-switch button:focus-visible,.expanded-toolbar button:focus-visible{outline:2px solid var(--primary);outline-offset:-2px;}
.map-selected{position:absolute;bottom:14px;left:14px;display:flex;gap:7px;align-items:center;padding:7px 10px;border-radius:3px;background:#fffffff5;border:1px solid #e2ddd6;color:#77736b;font-size:11px;pointer-events:none;}
.map-selected strong{font-size:12px;color:#76282c;font-weight:600;}
.selected-dot{width:5px;height:5px;border-radius:50%;background:#94070a;}
.map-footer{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 2px 2px;color:var(--muted);font-size:10px;line-height:1.5;}
.map-footer a,.expanded-note a{color:var(--muted);text-decoration:none;}
.map-footer a:hover,.expanded-note a:hover{color:var(--primary);text-decoration:underline;}
.map-feedback{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px;background:#f4f5f0;color:var(--muted);font-size:12px;}
.expanded-toolbar{display:flex;justify-content:space-between;gap:12px;margin-bottom:8px;}
.expanded-tabs{display:flex;padding:3px;background:#f5f3ee;border-radius:4px;}
.expanded-map{height:min(66vh,620px);min-height:330px;background:#f4f5f0;border-radius:4px;}
.expanded-note{display:flex;align-items:center;gap:15px;flex-wrap:wrap;margin-top:13px;font-size:12px;color:var(--muted);}
.expanded-note strong{color:var(--text);}
.expanded-note a{margin-left:auto;}
@media(max-width:650px){.dali-map.compact{--map-height:310px;}.map-footer{flex-wrap:wrap;gap:3px;}.map-selected span:last-child{display:none;}.expanded-map{min-height:300px;}.expanded-note{gap:6px;}.expanded-note a{margin-left:0;}}</style>
