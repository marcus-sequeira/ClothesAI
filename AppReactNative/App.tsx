import React, { useState, useRef, useEffect } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  Image,
  ActivityIndicator,
  Animated,
  Platform,
  Alert,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as ImagePicker from 'expo-image-picker';



// ─── Configuração da API ─────────────────────────────────────────────────────
// Usando o IP local do seu computador para funcionar tanto no simulador quanto no dispositivo físico
const API_URL = 'http://192.168.50.217:8080';

async function uploadToApi(
  imageUri: string,
): Promise<{ owner: string; confidence: number; image: string | null }> {
  const formData = new FormData();

  formData.append('image', {
    uri: imageUri,
    type: 'image/jpeg',
    name: 'photo.jpg',
  } as any);

  const response = await fetch(`${API_URL}/api/identify`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error || `Server error: ${response.status}`);
  }

  const data = await response.json();

  const topResult = Array.isArray(data.results) ? data.results[0] : data.results;

  return {
    owner: topResult?.owner ?? topResult?.name ?? 'Desconhecido',
    confidence: topResult?.probability ?? topResult?.confidence ?? topResult?.score ?? 0,
    image: topResult?.image ?? null,
  };
}

async function uploadImportToApi(
  imageUri: string,
  ownerName: string,
): Promise<{ success: boolean; owner: string }> {
  const formData = new FormData();

  formData.append('image', {
    uri: imageUri,
    type: 'image/jpeg',
    name: 'photo.jpg',
  } as any);

  formData.append('owner_name', ownerName);

  const response = await fetch(`${API_URL}/api/import`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error || `Server error: ${response.status}`);
  }

  const data = await response.json();

  return {
    success: data.success,
    owner: data.owner ?? ownerName,
  };
}

// ─── Definição de Tipos ──────────────────────────────────────────────────────────
type AppScreen = 'home' | 'loading' | 'result';

// ─── Componente Principal ──────────────────────────────────────────────────────
export default function App() {
  const [screen, setScreen] = useState<AppScreen>('home');
  const [mode, setMode] = useState<'import' | 'analyse'>('import');
  const [imageUri, setImageUri] = useState<string | null>(null);
  const [ownerResult, setOwnerResult] = useState<string | null>(null);
  const [ownerImage, setOwnerImage] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number>(0);
  const [isFlipped, setIsFlipped] = useState(false);

  // Animações
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(40)).current;
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const buttonScale1 = useRef(new Animated.Value(0)).current;
  const buttonScale2 = useRef(new Animated.Value(0)).current;
  const flipAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Animações de entrada na tela
    Animated.parallel([
      Animated.timing(fadeAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
      Animated.timing(slideAnim, { toValue: 0, duration: 800, useNativeDriver: true }),
      Animated.spring(buttonScale1, { toValue: 1, delay: 300, useNativeDriver: true }),
      Animated.spring(buttonScale2, { toValue: 1, delay: 500, useNativeDriver: true }),
    ]).start();
  }, []);

  useEffect(() => {
    if (screen === 'loading') {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.05, duration: 800, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
        ]),
      ).start();
    }
  }, [screen]);

  // ── Controle da Câmera ──────────────────────────────────────────────────────
  const launchCameraAndUpload = async (selectedMode: 'import' | 'analyse', ownerName?: string) => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert(
        'Permissão necessária',
        'Precisamos da permissão da câmera para continuar.',
      );
      return;
    }

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      allowsEditing: true,
      aspect: [3, 4],
      quality: 0.8,
    });

    if (!result.canceled && result.assets && result.assets.length > 0) {
      const uri = result.assets[0].uri;
      setImageUri(uri);
      setScreen('loading');

      try {
        if (selectedMode === 'import' && ownerName) {
          const importResult = await uploadImportToApi(uri, ownerName);
          setOwnerResult(importResult.owner);
          setOwnerImage(null);
          setConfidence(0);
        } else {
          const apiResult = await uploadToApi(uri);
          setOwnerResult(apiResult.owner);
          setOwnerImage(apiResult.image);
          setConfidence(apiResult.confidence);
        }
        setScreen('result');
      } catch (error: any) {
        console.error('API Error:', error);
        Alert.alert('Erro', error?.message || 'Não foi possível processar a imagem. Tente novamente.');
        resetApp();
      }
    }
  };

  const openCamera = (selectedMode: 'import' | 'analyse') => {
    setMode(selectedMode);

    if (selectedMode === 'import') {
      Alert.prompt(
        'Nome do dono',
        'Digite o nome do dono desta peça de roupa:',
        [
          { text: 'Cancelar', style: 'cancel' },
          {
            text: 'Continuar',
            onPress: (name?: string) => {
              if (name && name.trim()) {
                launchCameraAndUpload('import', name.trim());
              } else {
                Alert.alert('Erro', 'O nome do dono é obrigatório.');
              }
            },
          },
        ],
        'plain-text',
        '',
        'default',
      );
    } else {
      launchCameraAndUpload('analyse');
    }
  };

  // ── Animação de Giro do Cartão ──────────────────────────────────────────────
  const handleFlip = () => {
    const toValue = isFlipped ? 0 : 1;
    Animated.spring(flipAnim, {
      toValue,
      friction: 8,
      tension: 10,
      useNativeDriver: true,
    }).start();
    setIsFlipped(!isFlipped);
  };

  const frontInterpolate = flipAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '180deg'],
  });
  const backInterpolate = flipAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['180deg', '360deg'],
  });

  const resetApp = () => {
    setScreen('home');
    setImageUri(null);
    setOwnerResult(null);
    setOwnerImage(null);
    setConfidence(0);
    setIsFlipped(false);
    flipAnim.setValue(0);

    // Executar novamente as animações dos botões
    buttonScale1.setValue(0);
    buttonScale2.setValue(0);
    Animated.parallel([
      Animated.spring(buttonScale1, { toValue: 1, delay: 100, useNativeDriver: true }),
      Animated.spring(buttonScale2, { toValue: 1, delay: 250, useNativeDriver: true }),
    ]).start();
  };

  // ── Tela Inicial ─────────────────────────────────────────────────
  const renderHome = () => (
    <Animated.View
      style={[styles.content, { opacity: fadeAnim, transform: [{ translateY: slideAnim }] }]}
    >
      {/* Cabeçalho */}
      <View style={styles.headerContainer}>
        <Text style={styles.logoIcon}>👕</Text>
        <Text style={styles.title}>ClothesAI</Text>
        <Text style={styles.subtitle}>Identifique o dono de qualquer peça de roupa</Text>
      </View>

      {/* Botões de Ação */}
      <View style={styles.buttonsContainer}>
        <Animated.View style={{ transform: [{ scale: buttonScale1 }] }}>
          <TouchableOpacity
            activeOpacity={0.85}
            style={styles.actionButton}
            onPress={() => openCamera('import')}
          >
            <View style={styles.buttonGradient}>
              <Text style={styles.buttonIcon}>📸</Text>
              <View style={styles.buttonTextContainer}>
                <Text style={styles.buttonTitle}>Importar para a IA</Text>
                <Text style={styles.buttonDescription}>
                  Fotografe uma peça para registrar no sistema
                </Text>
              </View>
              <Text style={styles.buttonArrow}>›</Text>
            </View>
          </TouchableOpacity>
        </Animated.View>

        <Animated.View style={{ transform: [{ scale: buttonScale2 }] }}>
          <TouchableOpacity
            activeOpacity={0.85}
            style={[styles.actionButton, styles.actionButtonSecondary]}
            onPress={() => openCamera('analyse')}
          >
            <View style={styles.buttonGradient}>
              <Text style={styles.buttonIcon}>🔍</Text>
              <View style={styles.buttonTextContainer}>
                <Text style={styles.buttonTitle}>Analisar dono</Text>
                <Text style={styles.buttonDescription}>
                  Descubra de quem é a peça de roupa
                </Text>
              </View>
              <Text style={styles.buttonArrow}>›</Text>
            </View>
          </TouchableOpacity>
        </Animated.View>
      </View>

      {/* Dica no rodapé */}
      <Text style={styles.footerHint}>Toque em um botão para começar</Text>
    </Animated.View>
  );

  // ── Tela de Carregamento ──────────────────────────────────────────────
  const renderLoading = () => (
    <Animated.View style={[styles.content, styles.centeredContent, { transform: [{ scale: pulseAnim }] }]}>
      {imageUri && (
        <View style={styles.loadingImageContainer}>
          <Image source={{ uri: imageUri }} style={styles.loadingImage} />
          <View style={styles.loadingOverlay} />
        </View>
      )}
      <ActivityIndicator size="large" color="#8B5CF6" style={{ marginTop: 24 }} />
      <Text style={styles.loadingText}>Analisando…</Text>
      <Text style={styles.loadingSubtext}>
        {mode === 'import' ? 'Importando para o sistema de IA' : 'Identificando o dono da peça'}
      </Text>
    </Animated.View>
  );

  // ── Tela de Resultados ───────────────────────────────────────────────
  const renderResult = () => (
    <View style={[styles.content, styles.centeredContent]}>
      {imageUri && mode === 'analyse' && (
        <TouchableOpacity activeOpacity={1} onPress={handleFlip}>
          <View style={styles.flipCardContainer}>
            {/* Face Frontal – Foto Capturada */}
            <Animated.View
              style={[
                styles.resultImageContainer,
                styles.flipCardFace,
                { transform: [{ perspective: 1000 }, { rotateY: frontInterpolate }] },
              ]}
            >
              <Image source={{ uri: imageUri }} style={styles.resultImage} />
            </Animated.View>

            {/* Face Traseira – Resultado de quem é a peça */}
            <Animated.View
              style={[
                styles.resultImageContainer,
                styles.flipCardFace,
                styles.flipCardBack,
                { transform: [{ perspective: 1000 }, { rotateY: backInterpolate }] },
              ]}
            >
              <View style={styles.flipBackContent}>
                {ownerImage ? (
                  <Image source={{ uri: ownerImage }} style={styles.flipBackImage} />
                ) : (
                  <>
                    <View style={styles.flipBackGlow} />
                    <Text style={styles.flipBackIcon}>👤</Text>
                  </>
                )}
                <View style={styles.flipBackInfoOverlay}>
                  <Text style={styles.flipBackOwner}>{ownerResult}</Text>
                  <View style={styles.flipBackConfidenceBadge}>
                    <Text style={styles.flipBackConfidenceText}>{confidence}%</Text>
                  </View>
                </View>
              </View>
            </Animated.View>
          </View>
          <Text style={styles.flipHint}>
            {isFlipped ? 'Toque para ver a foto' : 'Toque para virar'}
          </Text>
        </TouchableOpacity>
      )}

      {imageUri && mode === 'import' && (
        <View style={styles.resultImageContainer}>
          <Image source={{ uri: imageUri }} style={styles.resultImage} />
        </View>
      )}

      <View style={styles.resultCard}>
        <Text style={styles.resultLabel}>
          {mode === 'import' ? '✅  Importado com sucesso' : '🔍  Resultado da análise'}
        </Text>
        <View style={styles.resultDivider} />
        <Text style={styles.resultOwnerLabel}>
          {mode === 'import' ? 'Registrado para' : 'Dono identificado'}
        </Text>
        <Text style={styles.resultOwnerName}>{ownerResult}</Text>
        {mode === 'analyse' && (
          <View style={styles.confidenceBadge}>
            <Text style={styles.confidenceText}>Confiança: {confidence}%</Text>
          </View>
        )}
      </View>

      <TouchableOpacity activeOpacity={0.85} style={styles.resetButton} onPress={resetApp}>
        <Text style={styles.resetButtonText}>Nova análise</Text>
      </TouchableOpacity>
    </View>
  );

  // ── Renderização Principal ─────────────────────────────────────────────────
  return (
    <View style={styles.container}>
      <StatusBar style="light" />
      {/* Bolinhas e decorações de fundo */}
      <View style={styles.bgDecoration1} />
      <View style={styles.bgDecoration2} />
      <View style={styles.bgDecoration3} />

      {screen === 'home' && renderHome()}
      {screen === 'loading' && renderLoading()}
      {screen === 'result' && renderResult()}
    </View>
  );
}

// ─── Estilos da Interface ───────────────────────────────────────────────────────
const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0F0F1A',
    overflow: 'hidden',
  },
  bgDecoration1: {
    position: 'absolute',
    width: 300,
    height: 300,
    borderRadius: 150,
    backgroundColor: 'rgba(139, 92, 246, 0.08)',
    top: -80,
    right: -80,
  },
  bgDecoration2: {
    position: 'absolute',
    width: 200,
    height: 200,
    borderRadius: 100,
    backgroundColor: 'rgba(59, 130, 246, 0.06)',
    bottom: 100,
    left: -60,
  },
  bgDecoration3: {
    position: 'absolute',
    width: 150,
    height: 150,
    borderRadius: 75,
    backgroundColor: 'rgba(236, 72, 153, 0.05)',
    bottom: -30,
    right: 40,
  },
  content: {
    flex: 1,
    paddingHorizontal: 24,
    paddingTop: Platform.OS === 'ios' ? 80 : 60,
    paddingBottom: 40,
  },
  centeredContent: {
    justifyContent: 'center',
    alignItems: 'center',
  },

  // ── Cabeçalho ───────────────────────
  headerContainer: {
    alignItems: 'center',
    marginBottom: 48,
  },
  logoIcon: {
    fontSize: 56,
    marginBottom: 12,
  },
  title: {
    fontSize: 34,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 1,
  },
  subtitle: {
    fontSize: 15,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 8,
    textAlign: 'center',
    maxWidth: 260,
    lineHeight: 22,
  },

  // ── Botões ──────────────────────
  buttonsContainer: {
    gap: 16,
  },
  actionButton: {
    backgroundColor: 'rgba(139, 92, 246, 0.12)',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(139, 92, 246, 0.25)',
  },
  actionButtonSecondary: {
    backgroundColor: 'rgba(59, 130, 246, 0.10)',
    borderColor: 'rgba(59, 130, 246, 0.22)',
  },
  buttonGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 20,
    paddingHorizontal: 20,
  },
  buttonIcon: {
    fontSize: 32,
    marginRight: 16,
  },
  buttonTextContainer: {
    flex: 1,
  },
  buttonTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#FFFFFF',
    marginBottom: 4,
  },
  buttonDescription: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.45)',
    lineHeight: 18,
  },
  buttonArrow: {
    fontSize: 28,
    color: 'rgba(255,255,255,0.25)',
    fontWeight: '300',
  },

  footerHint: {
    textAlign: 'center',
    color: 'rgba(255,255,255,0.2)',
    fontSize: 13,
    marginTop: 32,
  },

  // ── Carregamento ──────────────────────
  loadingImageContainer: {
    width: 200,
    height: 260,
    borderRadius: 20,
    overflow: 'hidden',
    position: 'relative',
  },
  loadingImage: {
    width: '100%',
    height: '100%',
    resizeMode: 'cover',
  },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(15, 15, 26, 0.5)',
  },
  loadingText: {
    fontSize: 22,
    fontWeight: '700',
    color: '#FFFFFF',
    marginTop: 16,
  },
  loadingSubtext: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.4)',
    marginTop: 6,
    textAlign: 'center',
    maxWidth: 240,
  },

  // ── Estilos de Resultado ───────────────────────
  flipCardContainer: {
    width: 200,
    height: 260,
    alignItems: 'center',
    justifyContent: 'center',
  },
  flipCardFace: {
    backfaceVisibility: 'hidden',
  },
  flipCardBack: {
    position: 'absolute',
    top: 0,
  },
  flipBackContent: {
    flex: 1,
    backgroundColor: '#1A1A2E',
    alignItems: 'center',
    justifyContent: 'center',
  },
  flipBackImage: {
    ...StyleSheet.absoluteFillObject,
    width: '100%',
    height: '100%',
    resizeMode: 'cover',
  },
  flipBackInfoOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(15, 15, 26, 0.75)',
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
  },
  flipBackGlow: {
    position: 'absolute',
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: 'rgba(139, 92, 246, 0.15)',
  },
  flipBackIcon: {
    fontSize: 56,
    marginBottom: 12,
  },
  flipBackOwner: {
    fontSize: 22,
    fontWeight: '800',
    color: '#FFFFFF',
    textAlign: 'center',
    marginBottom: 10,
  },
  flipBackConfidenceBadge: {
    backgroundColor: 'rgba(34, 197, 94, 0.15)',
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(34, 197, 94, 0.3)',
  },
  flipBackConfidenceText: {
    color: '#4ADE80',
    fontSize: 14,
    fontWeight: '700',
  },
  flipHint: {
    textAlign: 'center',
    color: 'rgba(255,255,255,0.35)',
    fontSize: 12,
    marginTop: 8,
    marginBottom: 16,
  },
  resultImageContainer: {
    width: 200,
    height: 260,
    borderRadius: 20,
    overflow: 'hidden',
    borderWidth: 2,
    borderColor: 'rgba(139, 92, 246, 0.4)',
  },
  resultImage: {
    width: '100%',
    height: '100%',
    resizeMode: 'cover',
  },
  resultCard: {
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 20,
    padding: 24,
    width: '100%',
    maxWidth: 340,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  resultLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: '#A78BFA',
  },
  resultDivider: {
    height: 1,
    width: 60,
    backgroundColor: 'rgba(255,255,255,0.1)',
    marginVertical: 16,
  },
  resultOwnerLabel: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.4)',
    textTransform: 'uppercase',
    letterSpacing: 2,
    marginBottom: 6,
  },
  resultOwnerName: {
    fontSize: 36,
    fontWeight: '800',
    color: '#FFFFFF',
    marginBottom: 12,
  },
  confidenceBadge: {
    backgroundColor: 'rgba(34, 197, 94, 0.15)',
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(34, 197, 94, 0.3)',
  },
  confidenceText: {
    color: '#4ADE80',
    fontSize: 13,
    fontWeight: '600',
  },

  // ── Botão de Voltar ao Início ─────────────────
  resetButton: {
    marginTop: 28,
    backgroundColor: 'rgba(139, 92, 246, 0.15)',
    paddingVertical: 16,
    paddingHorizontal: 48,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(139, 92, 246, 0.3)',
  },
  resetButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#A78BFA',
  },
});
