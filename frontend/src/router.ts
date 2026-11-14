import {createRouter,createWebHistory} from 'vue-router';
import WorkbenchView from './views/WorkbenchView.vue';
import DataView from './views/DataView.vue';
import FeaturesView from './views/FeaturesView.vue';
import EvaluationView from './views/EvaluationView.vue';
import DecompositionView from './views/DecompositionView.vue';
import HistoryView from './views/HistoryView.vue';
export default createRouter({history:createWebHistory(),routes:[{path:'/',redirect:'/workbench'},{path:'/workbench',component:WorkbenchView},{path:'/embed/workbench',component:WorkbenchView},{path:'/history',component:HistoryView},{path:'/data',component:DataView},{path:'/features',component:FeaturesView},{path:'/evaluation',component:EvaluationView},{path:'/decomposition',component:DecompositionView}]});
