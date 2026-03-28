import { registerRootComponent } from 'expo';

import App from './App';

// registerRootComponent chama AppRegistry.registerComponent('main', () => App);
// Ele também garante que, ao rodar no Expo Go ou em build nativo,
// o ambiente esteja configurado corretamente
registerRootComponent(App);
