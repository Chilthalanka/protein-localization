import torch
import torch.nn as nn
import torch.nn.functional as F

from models.multi_head_attention import MultiHeadAttention


class ConvSelfAttention(nn.Module):
    def __init__(self, embeddings_dim: int = 1024, dropout=0.25, kernel_size=7, attention_dropout=0.25, n_heads=8):
        super(ConvSelfAttention, self).__init__()

        self.conv1 = nn.Conv1d(embeddings_dim, embeddings_dim, kernel_size=kernel_size, stride=1,
                               padding=0)
        self.multi_head_attention = MultiHeadAttention(embeddings_dim, attention_dropout, n_heads,
                                                       skip_last_linear=True)

        self.linear = nn.Sequential(
            nn.Linear(2 * embeddings_dim, 32),
            nn.Dropout(dropout),
            nn.ReLU(),
            nn.BatchNorm1d(32)
        )
        self.output = nn.Linear(32, 11)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, embeddings_dim, sequence_length] embedding tensor that should be classified

        Returns:
            classification: [batch_size,output_dim] tensor with logits
        """
        o = F.relu(self.conv1(x))

        o = o.permute(0, 2, 1)  # [batch_size, sequence_length, embeddings_dim]

        o, _ = self.multi_head_attention(o, o, o)  # [batch_size, seq_len , embeddings_dim]
        o1 = torch.mean(o, dim=-2)
        o2, _ = torch.max(o, dim=-2)
        o = torch.cat([o1, o2], dim=-1)
        o = self.linear(o)
        return self.output(o)